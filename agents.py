# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mes IA et nouveau tchat (0.9.0).

Une « IA » = un modèle Ollama de base + un rôle en 4 sections (Contexte, Tâche, Contraintes, Format)
+ des documents de la bibliothèque. Phase 1 de l'entraînement : les poids du modèle ne changent pas.
Les documents sont découpés en extraits, transformés en vecteurs (EMBED_MODEL), et les extraits les plus
proches de chaque question sont ajoutés au message (RAG). Sans vecteurs, une recherche par mots prend le relais.
Le rôle peut aussi être publié comme vrai modèle Ollama (« ia-<id>-<nom> »), utilisable dans Open WebUI.

Les conversations du tchat sont enregistrées par compte. Cibles possibles :
« agent:<id> » (une de mes IA), « ollama:<modèle> », « claude:haiku » / « claude:sonnet » (clé API du compte).
"""
import array
import hashlib
import json
import math
import re
import time
import unicodedata

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import chat
from core import (EMBED_MODEL, NUM_CTX, PROMPT_MODEL, RAG_TOP_K, db, ndjson, now_iso, ollama_json,
                  ollama_stream, strip_fence, tokens)
from library import kept_text
from prompts import build_message

router = APIRouter(prefix="/api/ia")

CHUNK_CHARS, CHUNK_OVERLAP = 1200, 150   # extraits d'environ 300 tokens, qui se chevauchent un peu
ANSWER_RESERVE = 1500                    # tokens laissés libres pour la réponse dans num_ctx
EMBED_BATCH = 16
INDEX_ERRORS: dict[int, str] = {}        # dernière erreur d'indexation par document (perdue au redémarrage)
SECTIONS = (("context", "Contexte"), ("task", "Tâche"), ("constraints", "Contraintes"), ("format", "Format"))
RAG_RULES = ("Des extraits de documents accompagnent la question. Appuie-toi d'abord sur eux, cite-les "
             "par leur numéro ([1], [2]…). S'ils ne contiennent pas la réponse, dis-le clairement "
             "au lieu d'inventer.")
STOPWORDS = set("""les des une est pas pour que qui dans par sur avec son ses aux mais ont été sont cette
ces tout tous elle ils leur nous vous mon ton sans plus comme fait faire entre être avoir dont quoi quel
quelle the and for are with this that from""".split())


# ---------- Outils ----------
def slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40] or "ia"


def words(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return [w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in STOPWORDS]


def system_prompt(agent: dict, with_docs: bool) -> str:
    parts = [f"## {label}\n{(agent.get(key) or '').strip()}" for key, label in SECTIONS
             if (agent.get(key) or "").strip()]
    if with_docs:
        parts.append(RAG_RULES)
    return "\n\n".join(parts)


def parse_target(target: str) -> tuple[str, str]:
    kind, _, value = (target or "").partition(":")
    if kind not in ("agent", "ollama", "claude") or not value.strip():
        raise ValueError("Cible inconnue (agent:<id>, ollama:<modèle> ou claude:haiku).")
    return kind, value.strip()


# ---------- Découpage et vecteurs ----------
def chunk_sections(sections: list[dict]) -> list[tuple[str, str]]:
    """Découpe les sections gardées en extraits d'au plus CHUNK_CHARS caractères, au niveau des paragraphes."""
    out = []
    for s in sections:
        if not s.get("on"):
            continue
        title, buf = s.get("title") or "", ""
        paras = [p.strip() for p in re.split(r"\n\s*\n", s["text"]) if p.strip()]
        pieces = []
        for p in paras:  # un paragraphe trop long est coupé net
            pieces += [p[i:i + CHUNK_CHARS] for i in range(0, len(p), CHUNK_CHARS)] if len(p) > CHUNK_CHARS else [p]
        for p in pieces:
            if buf and len(buf) + len(p) + 2 > CHUNK_CHARS:
                out.append((title, buf))
                buf = buf[-CHUNK_OVERLAP:].split(" ", 1)[-1] + "\n\n" + p
            else:
                buf = f"{buf}\n\n{p}" if buf else p
        if buf.strip():
            out.append((title, buf))
    return out


def pack(vec: list[float]) -> bytes:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array.array("f", (x / n for x in vec)).tobytes()


def unpack(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    return a


def embed(texts: list[str]) -> list[list[float]]:
    r = ollama_json("/api/embed", {"model": EMBED_MODEL, "input": texts}, timeout=180)
    vecs = r.get("embeddings") or []
    if len(vecs) != len(texts):
        raise RuntimeError(r.get("error") or "réponse inattendue d'Ollama pour les vecteurs")
    return vecs


def ensure_chunks(con, doc_id: int) -> bool:
    """(Re)découpe un document prêt si son texte a changé. Renvoie False si le document n'est pas prêt."""
    r = con.execute("SELECT status, sections FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not r or r["status"] != "prêt":
        return False
    sections = json.loads(r["sections"] or "[]")
    h = hashlib.sha1(kept_text(sections).encode()).hexdigest()
    old = con.execute("SELECT doc_hash FROM chunks WHERE document_id=? LIMIT 1", (doc_id,)).fetchone()
    if old and old["doc_hash"] == h:
        return True
    con.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
    con.executemany("INSERT INTO chunks(document_id, idx, doc_hash, title, text) VALUES (?,?,?,?,?)",
                    [(doc_id, i, h, t, txt) for i, (t, txt) in enumerate(chunk_sections(sections))])
    return True


def index_documents(doc_ids: list[int]):
    """Tâche de fond : découpe les documents et calcule les vecteurs qui manquent."""
    for doc_id in dict.fromkeys(doc_ids):
        try:
            with db() as con:
                if not ensure_chunks(con, doc_id):
                    continue
                todo = con.execute("SELECT id, title, text FROM chunks WHERE document_id=? AND "
                                   "(vector IS NULL OR embed_model IS NOT ?)", (doc_id, EMBED_MODEL)).fetchall()
            for i in range(0, len(todo), EMBED_BATCH):
                batch = todo[i:i + EMBED_BATCH]
                vecs = embed([f"{c['title']}\n{c['text']}" for c in batch])
                with db() as con:
                    con.executemany("UPDATE chunks SET vector=?, embed_model=? WHERE id=?",
                                    [(pack(v), EMBED_MODEL, c["id"]) for c, v in zip(batch, vecs)])
            INDEX_ERRORS.pop(doc_id, None)
        except Exception as e:
            INDEX_ERRORS[doc_id] = f"{EMBED_MODEL} : {e}"[:300]


def index_status(con, doc_ids: list[int]) -> dict:
    if not doc_ids:
        return {"chunks": 0, "vectors": 0, "errors": []}
    q = ",".join("?" * len(doc_ids))
    r = con.execute(f"SELECT COUNT(*), SUM(vector IS NOT NULL AND embed_model=?) FROM chunks "
                    f"WHERE document_id IN ({q})", (EMBED_MODEL, *doc_ids)).fetchone()
    return {"chunks": r[0] or 0, "vectors": r[1] or 0,
            "errors": [INDEX_ERRORS[d] for d in doc_ids if d in INDEX_ERRORS]}


def search(con, doc_ids: list[int], query: str, k: int = RAG_TOP_K) -> tuple[list[dict], str]:
    """Les k extraits les plus proches de la question. Renvoie (extraits, méthode « vecteurs » ou « mots »)."""
    ready = [d for d in dict.fromkeys(doc_ids) if ensure_chunks(con, d)]
    if not ready or not query.strip():
        return [], ""
    q = ",".join("?" * len(ready))
    rows = con.execute(f"SELECT c.id, c.title, c.text, c.vector, c.embed_model, d.name FROM chunks c "
                       f"JOIN documents d ON d.id=c.document_id WHERE c.document_id IN ({q})", ready).fetchall()
    if not rows:
        return [], ""
    scored, method = None, "mots"
    if all(r["vector"] is not None and r["embed_model"] == EMBED_MODEL for r in rows):
        try:
            qv = unpack(pack(embed([query])[0]))
            scored = [(sum(a * b for a, b in zip(qv, unpack(r["vector"]))), r) for r in rows]
            method = "vecteurs"
        except Exception:
            scored = None
    if scored is None:  # recherche par mots, façon TF-IDF
        qw = set(words(query))
        docs_w = [words(f"{r['title']} {r['text']}") for r in rows]
        df = {w: sum(1 for dw in docs_w if w in dw) for w in qw}
        scored = []
        for r, dw in zip(rows, docs_w):
            s = sum(math.log(1 + len(rows) / df[w]) * (1 + math.log(dw.count(w))) for w in qw if w in dw)
            scored.append((s / math.sqrt(len(dw) or 1), r))
        scored = [x for x in scored if x[0] > 0]
    scored.sort(key=lambda x: -x[0])
    return [{"doc": r["name"], "title": r["title"], "text": r["text"], "score": round(s, 3)}
            for s, r in scored[:k]], method


def with_excerpts(question: str, excerpts: list[dict]) -> str:
    if not excerpts:
        return question
    blocs = "\n\n".join(f"[{i}] {e['doc']} › {e['title']}\n{e['text']}" for i, e in enumerate(excerpts, 1))
    return f"Extraits des documents :\n\n{blocs}\n\n---\nQuestion : {question}"


def fit_history(msgs: list[dict], budget: int) -> list[dict]:
    """Retire les plus anciens messages tant que la conversation dépasse le budget (le dernier reste)."""
    msgs = list(msgs)
    while len(msgs) > 1 and sum(tokens(m["content"]) for m in msgs) > budget:
        msgs.pop(0)
    while msgs and msgs[0]["role"] != "user":  # une conversation commence par l'utilisateur
        msgs.pop(0)
    return msgs


# ---------- Modèles disponibles ----------
def is_embedding(m: dict) -> bool:
    fam = {(m.get("details") or {}).get("family", "")} | set((m.get("details") or {}).get("families") or [])
    name = m.get("name", "")
    return "embed" in name or name.startswith("bge") or bool(fam & {"bert", "nomic-bert", "xlm-roberta"})


@router.get("/models")
def models(request: Request):
    uid = request.state.user["id"]
    out = {"ollama": [], "ollama_error": None, "embed_model": EMBED_MODEL, "embed_ready": False,
           "claude": list(chat.MODELS)}
    try:
        tags = ollama_json("/api/tags").get("models", [])
        out["ollama"] = sorted(({"name": m["name"], "size": m.get("size", 0),
                                 "params": (m.get("details") or {}).get("parameter_size", "")}
                                for m in tags if not is_embedding(m)), key=lambda m: m["name"])
        out["embed_ready"] = any(m["name"].split(":")[0] == EMBED_MODEL.split(":")[0] for m in tags)
    except Exception as e:
        out["ollama_error"] = str(e) or type(e).__name__
    with db() as con:
        out["agents"] = [dict(r) for r in con.execute(
            "SELECT id, name, base_model FROM agents WHERE owner_id=? ORDER BY name", (uid,))]
        out["has_key"] = bool(chat.settings_of(con, uid)["api_key_enc"])
    return out


# ---------- Mes IA ----------
class AgentIn(BaseModel):
    name: str
    base_model: str
    context: str = ""
    task: str = ""
    constraints: str = ""
    format: str = ""
    temperature: float = 0.4
    doc_ids: list[int] = []


def agent_row(con, agent_id: int, uid: int):
    return con.execute("SELECT * FROM agents WHERE id=? AND owner_id=?", (agent_id, uid)).fetchone()


def agent_doc_ids(con, agent_id: int) -> list[int]:
    return [r[0] for r in con.execute("SELECT document_id FROM agent_documents WHERE agent_id=? "
                                      "ORDER BY document_id", (agent_id,))]


def agent_dict(con, r) -> dict:
    d = dict(r)
    d["doc_ids"] = agent_doc_ids(con, r["id"])
    d["docs"] = [dict(x) for x in con.execute(
        "SELECT d.id, d.name, d.status FROM agent_documents a JOIN documents d ON d.id=a.document_id "
        "WHERE a.agent_id=? ORDER BY d.name", (r["id"],))]
    d["index"] = index_status(con, d["doc_ids"])
    d["system"] = system_prompt(d, bool(d["doc_ids"]))
    return d


def check_agent(body: AgentIn, con):
    if not body.name.strip():
        return "Donnez un nom à l'IA."
    if not body.base_model.strip():
        return "Choisissez un modèle de base."
    if not 0 <= body.temperature <= 2:
        return "Température entre 0 et 2."
    for d in dict.fromkeys(body.doc_ids):
        if not con.execute("SELECT 1 FROM documents WHERE id=?", (d,)).fetchone():
            return f"Document {d} introuvable."
    return None


def save_docs(con, agent_id: int, doc_ids: list[int]):
    con.execute("DELETE FROM agent_documents WHERE agent_id=?", (agent_id,))
    con.executemany("INSERT INTO agent_documents(agent_id, document_id) VALUES (?,?)",
                    [(agent_id, d) for d in dict.fromkeys(doc_ids)])


@router.get("/agents")
def list_agents(request: Request):
    with db() as con:
        rows = con.execute("SELECT * FROM agents WHERE owner_id=? ORDER BY name", (request.state.user["id"],))
        return [agent_dict(con, r) for r in rows.fetchall()]


@router.post("/agents")
def create_agent(body: AgentIn, request: Request, bg: BackgroundTasks):
    with db() as con:
        if err := check_agent(body, con):
            return JSONResponse({"error": err}, 400)
        now = now_iso()
        cur = con.execute(
            "INSERT INTO agents(owner_id, name, base_model, context, task, constraints, format, temperature, "
            "created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (request.state.user["id"], body.name.strip()[:60], body.base_model.strip(), body.context.strip(),
             body.task.strip(), body.constraints.strip(), body.format.strip(), body.temperature, now, now))
        save_docs(con, cur.lastrowid, body.doc_ids)
        r = agent_row(con, cur.lastrowid, request.state.user["id"])
        out = agent_dict(con, r)
    bg.add_task(index_documents, body.doc_ids)
    return out


@router.get("/agents/{agent_id}")
def get_agent(agent_id: int, request: Request):
    with db() as con:
        r = agent_row(con, agent_id, request.state.user["id"])
        return agent_dict(con, r) if r else JSONResponse({"error": "IA introuvable."}, 404)


@router.put("/agents/{agent_id}")
def edit_agent(agent_id: int, body: AgentIn, request: Request, bg: BackgroundTasks):
    uid = request.state.user["id"]
    with db() as con:
        if not agent_row(con, agent_id, uid):
            return JSONResponse({"error": "IA introuvable."}, 404)
        if err := check_agent(body, con):
            return JSONResponse({"error": err}, 400)
        con.execute("UPDATE agents SET name=?, base_model=?, context=?, task=?, constraints=?, format=?, "
                    "temperature=?, updated=? WHERE id=?",
                    (body.name.strip()[:60], body.base_model.strip(), body.context.strip(), body.task.strip(),
                     body.constraints.strip(), body.format.strip(), body.temperature, now_iso(), agent_id))
        save_docs(con, agent_id, body.doc_ids)
        out = agent_dict(con, agent_row(con, agent_id, uid))
    bg.add_task(index_documents, body.doc_ids)
    return out


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: int, request: Request):
    with db() as con:
        r = agent_row(con, agent_id, request.state.user["id"])
        if not r:
            return JSONResponse({"error": "IA introuvable."}, 404)
        con.execute("DELETE FROM agents WHERE id=?", (agent_id,))
    removed = None
    if r["ollama_model"]:  # on retire aussi le modèle publié dans Ollama (sans bloquer si Ollama est absent)
        try:
            ollama_json("/api/delete", {"model": r["ollama_model"]}, timeout=30, method="DELETE")
            removed = r["ollama_model"]
        except Exception:
            removed = None
    return {"ok": True, "ollama_removed": removed}


@router.post("/agents/{agent_id}/index")
def reindex_agent(agent_id: int, request: Request, bg: BackgroundTasks):
    with db() as con:
        if not agent_row(con, agent_id, request.state.user["id"]):
            return JSONResponse({"error": "IA introuvable."}, 404)
        ids = agent_doc_ids(con, agent_id)
    for d in ids:
        INDEX_ERRORS.pop(d, None)
    bg.add_task(index_documents, ids)
    return {"ok": True, "documents": len(ids)}


@router.post("/agents/{agent_id}/publish")
def publish_agent(agent_id: int, request: Request):
    """Crée (ou recrée) le modèle Ollama « ia-<id>-<nom> » : modèle de base + rôle, sans les documents."""
    uid = request.state.user["id"]
    with db() as con:
        r = agent_row(con, agent_id, uid)
        if not r:
            return JSONResponse({"error": "IA introuvable."}, 404)
        a = dict(r)
    name = f"ia-{agent_id}-{slug(a['name'])}"
    payload = {"model": name, "from": a["base_model"], "system": system_prompt(a, False),
               "parameters": {"temperature": a["temperature"]}, "stream": False}
    try:
        res = ollama_json("/api/create", payload, timeout=600)
        if res.get("error"):
            raise RuntimeError(res["error"])
    except Exception as e:
        return JSONResponse({"error": f"Ollama : {e}"}, 502)
    with db() as con:
        con.execute("UPDATE agents SET ollama_model=?, published=? WHERE id=?", (name, now_iso(), agent_id))
        return agent_dict(con, agent_row(con, agent_id, uid))


# ---------- Conversations ----------
def conv_row(con, conv_id: int, uid: int):
    return con.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, uid)).fetchone()


def conv_dict(r) -> dict:
    d = dict(r)
    d["doc_ids"] = json.loads(r["doc_ids"] or "[]")
    return d


@router.get("/conversations")
def list_conversations(request: Request):
    with db() as con:
        rows = con.execute("SELECT * FROM conversations WHERE user_id=? ORDER BY updated DESC, id DESC LIMIT 300",
                           (request.state.user["id"],)).fetchall()
    return [conv_dict(r) for r in rows]


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: int, request: Request):
    with db() as con:
        r = conv_row(con, conv_id, request.state.user["id"])
        if not r:
            return JSONResponse({"error": "Conversation introuvable."}, 404)
        d = conv_dict(r)
        d["messages"] = [{"id": m["id"], "role": m["role"], "content": m["content"],
                          "meta": json.loads(m["meta"] or "{}"), "date": m["date"]}
                         for m in con.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id",
                                              (conv_id,))]
        d["docs"] = [dict(x) for x in con.execute(
            f"SELECT id, name, status FROM documents WHERE id IN ({','.join('?' * len(d['doc_ids']))})",
            d["doc_ids"])] if d["doc_ids"] else []
    return d


class ConvPatch(BaseModel):
    title: str | None = None
    target: str | None = None
    doc_ids: list[int] | None = None


@router.patch("/conversations/{conv_id}")
def edit_conversation(conv_id: int, body: ConvPatch, request: Request, bg: BackgroundTasks):
    with db() as con:
        if not conv_row(con, conv_id, request.state.user["id"]):
            return JSONResponse({"error": "Conversation introuvable."}, 404)
        if body.title is not None and body.title.strip():
            con.execute("UPDATE conversations SET title=? WHERE id=?", (body.title.strip()[:120], conv_id))
        if body.target is not None:
            try:
                parse_target(body.target)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, 400)
            con.execute("UPDATE conversations SET target=? WHERE id=?", (body.target, conv_id))
        if body.doc_ids is not None:
            ids = list(dict.fromkeys(body.doc_ids))[:20]
            con.execute("UPDATE conversations SET doc_ids=? WHERE id=?", (json.dumps(ids), conv_id))
            bg.add_task(index_documents, ids)
    return get_conversation(conv_id, request)


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: int, request: Request):
    with db() as con:
        con.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (conv_id, request.state.user["id"]))
    return {"ok": True}


# ---------- Envoi d'un message ----------
class ChatIn(BaseModel):
    target: str
    message: str = ""
    conversation_id: int | None = None
    doc_ids: list[int] | None = None
    regenerate: bool = False


@router.post("/chat")
def send(body: ChatIn, request: Request):
    uid = request.state.user["id"]
    try:
        kind, value = parse_target(body.target)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)
    text = body.message.strip()
    if not body.regenerate and not text:
        return JSONResponse({"error": "Écrivez un message."}, 400)
    if kind == "claude":  # plafond et clé vérifiés avant d'enregistrer quoi que ce soit
        if value not in chat.MODELS:
            return JSONResponse({"error": "Modèle Claude inconnu."}, 400)
        err, key, price = chat.prepare_claude(uid, value)
        if err:
            return err

    with db() as con:
        agent = None
        if kind == "agent":
            r = agent_row(con, int(value) if value.isdigit() else -1, uid)
            if not r:
                return JSONResponse({"error": "IA introuvable."}, 404)
            agent = dict(r)
            agent["doc_ids"] = agent_doc_ids(con, r["id"])
        # Conversation : existante ou nouvelle
        now = now_iso()
        if body.conversation_id:
            conv = conv_row(con, body.conversation_id, uid)
            if not conv:
                return JSONResponse({"error": "Conversation introuvable."}, 404)
            conv_id = conv["id"]
            doc_ids = json.loads(conv["doc_ids"] or "[]") if body.doc_ids is None else body.doc_ids
        else:
            if body.regenerate:
                return JSONResponse({"error": "Rien à régénérer."}, 400)
            doc_ids = body.doc_ids or []
            plain = re.sub(r"(?m)^#{1,6}\s.*$", "", text)  # message structuré : sans les titres ## Contexte…
            title = re.sub(r"\s+", " ", plain if plain.strip() else text).strip()[:60] or "Nouvelle conversation"
            conv_id = con.execute("INSERT INTO conversations(user_id, title, target, doc_ids, created, updated) "
                                  "VALUES (?,?,?,?,?,?)", (uid, title, body.target, "[]", now, now)).lastrowid
        doc_ids = list(dict.fromkeys(doc_ids))[:20]
        con.execute("UPDATE conversations SET target=?, doc_ids=?, updated=? WHERE id=?",
                    (body.target, json.dumps(doc_ids), now, conv_id))
        if body.regenerate:  # on retire la dernière réponse et on repart du dernier message de l'utilisateur
            last = con.execute("SELECT id, role FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
                               (conv_id,)).fetchone()
            if last and last["role"] == "assistant":
                con.execute("DELETE FROM messages WHERE id=?", (last["id"],))
        else:
            con.execute("INSERT INTO messages(conversation_id, role, content, date) VALUES (?, 'user', ?, ?)",
                        (conv_id, text, now))
        history = [{"role": m["role"], "content": m["content"]} for m in con.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id", (conv_id,))]
        if not history or history[-1]["role"] != "user":
            return JSONResponse({"error": "Le dernier message doit venir de vous."}, 400)
        question = history[-1]["content"]
        all_docs = list(dict.fromkeys((agent["doc_ids"] if agent else []) + doc_ids))
        excerpts, method = search(con, all_docs, question)
        title = con.execute("SELECT title FROM conversations WHERE id=?", (conv_id,)).fetchone()[0]

    # Messages envoyés au modèle : rôle de l'IA, historique tronqué, extraits ajoutés à la dernière question
    system = system_prompt(agent, bool(all_docs)) if agent else (RAG_RULES if all_docs else "")
    history[-1] = {"role": "user", "content": with_excerpts(question, excerpts)}
    msgs = fit_history(history, NUM_CTX - ANSWER_RESERVE - tokens(system))
    sources = [{"n": i, "doc": e["doc"], "title": e["title"], "excerpt": e["text"][:300]}
               for i, e in enumerate(excerpts, 1)]

    if kind == "claude":
        if system:  # l'API Claude de chat.py n'a pas de champ « system » : consigne ajoutée à la question
            msgs[-1] = {"role": "user", "content": f"{system}\n\n{msgs[-1]['content']}"}
        events = chat.claude_events(uid, value, msgs, key, price)
        model_label = f"Claude {value}"
    else:
        model = agent["base_model"] if agent else value
        if system:
            msgs = [{"role": "system", "content": system}] + msgs
        options = {"num_ctx": NUM_CTX}
        if agent:
            options["temperature"] = agent["temperature"]
        events = ollama_events(model, msgs, options)
        model_label = f"{agent['name']} ({model})" if agent else model

    def stream():
        yield ndjson({"conversation_id": conv_id, "title": title, "sources": sources, "search": method,
                      "model": model_label})
        out, meta, t0 = [], {"model": model_label, "sources": sources, "search": method}, time.time()
        for ev in events:
            if "t" in ev:
                out.append(ev["t"])
            if "error" in ev:
                meta["error"] = ev["error"]
            if ev.get("done"):
                meta.update({k: ev[k] for k in ("tokens_in", "tokens_out", "cost", "fallback", "reason",
                                                "month_cost", "month_tokens") if k in ev})
                continue
            yield ndjson(ev)
        meta["duration_s"] = round(time.time() - t0, 1)
        answer = "".join(out).strip()
        with db() as con:
            mid = con.execute("INSERT INTO messages(conversation_id, role, content, meta, date) "
                              "VALUES (?, 'assistant', ?, ?, ?)",
                              (conv_id, answer or "(aucune réponse)", json.dumps(meta, ensure_ascii=False),
                               now_iso())).lastrowid
            con.execute("UPDATE conversations SET updated=? WHERE id=?", (now_iso(), conv_id))
        yield ndjson({"done": True, "message_id": mid, "conversation_id": conv_id, **meta})

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def ollama_events(model: str, msgs: list, options: dict):
    out = []
    try:
        for chunk in ollama_stream(model, msgs, options=options):
            out.append(chunk)
            yield {"t": chunk}
    except Exception as e:
        yield {"error": f"Ollama ({model}) : {e}"}
    yield {"done": True, "tokens_in": sum(tokens(m["content"]) for m in msgs), "tokens_out": tokens("".join(out)),
           "cost": 0.0}


# ---------- Structurer un message avec le Prompteur ----------
class StructureIn(BaseModel):
    text: str
    context_id: int | None = None


@router.post("/structure")
def structure(body: StructureIn):
    """Met un message brut au format Contexte / Tâche / Contraintes / Format, avec le modèle du Prompteur."""
    if not body.text.strip():
        return JSONResponse({"error": "Écrivez d'abord votre idée."}, 400)
    ctx = ""
    if body.context_id:
        with db() as con:
            r = con.execute("SELECT text FROM contexts WHERE id=?", (body.context_id,)).fetchone()
            ctx = r["text"] if r else ""
    message = build_message(body.text, ctx, [], False)

    def stream():
        parts = []
        try:
            for chunk in ollama_stream(PROMPT_MODEL, [{"role": "user", "content": message}]):
                parts.append(chunk)
                yield ndjson({"t": chunk})
        except Exception as e:
            yield ndjson({"error": f"Prompteur injoignable : {e}"})
            return
        yield ndjson({"done": True, "prompt": strip_fence("".join(parts))})

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
