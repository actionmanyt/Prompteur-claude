# SPDX-License-Identifier: AGPL-3.0-or-later
"""Génération du prompt par Ollama (streaming) et historique."""
import json
import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core import DOC_BUDGET, PROMPT_MODEL, OllamaError, db, ndjson, now_iso, ollama_stream, strip_fence, tokens
from library import kept_text

router = APIRouter(prefix="/api")
ANSWER_MARGIN = 500  # tokens à laisser libres : réponse (num_predict 400 du Modelfile) et gabarit qwen3
TOO_LONG = re.compile(r"request \((\d+) tokens\) exceeds the available context size \((\d+) tokens\)")


def ollama_message(e: Exception) -> str:
    """0.7.1 : message clair quand Ollama refuse, avec la part à retirer si le texte est trop long."""
    m = TOO_LONG.search(str(e))
    if m:
        real, avail = int(m[1]), int(m[2])
        cut = -(-100 * (real - (avail - ANSWER_MARGIN)) // real)  # arrondi au-dessus
        return (f"Trop long pour le modèle : {real} tokens réels pour {avail} disponibles. "
                f"Retirez environ {cut} % des sections (ou un document), puis relancez.")
    return f"Ollama : {e}"


class GenerateIn(BaseModel):
    idea: str
    doc_ids: list[int] = []
    context_id: int | None = None
    attach: bool = True


def build_message(idea: str, ctx: str, docs: list[dict], attach: bool) -> str:
    parts = [idea.strip()]
    if ctx:
        parts.append("Mon contexte (à reprendre en bref dans la section Contexte) :\n" + ctx)
    if docs:
        blocs = "\n\n".join(f"### Document {i} : {d['name']}\n{d['text']}" for i, d in enumerate(docs, 1))
        parts.append("Documents fournis. Résume fidèlement chacun en 1 à 2 phrases dans la section Contexte, "
                     "sans rien inventer :\n\n" + blocs)
        if len(docs) > 1:
            parts.append("Ajoute dans les Contraintes : citer le document concerné (Document 1, 2…) pour chaque "
                         "point, et signaler les contradictions entre documents.")
        if attach:
            parts.append("[Info technique, à ne pas mentionner] Les documents seront ajoutés après ton prompt. "
                         "N'écris aucune section Document. Dans la Tâche, parle des « documents ci-dessous ».")
    return "\n\n".join(parts)


def appendix(docs: list[dict]) -> str:
    out = []
    for i, d in enumerate(docs, 1):
        body = re.sub(r"^(#{1,5}) ", r"#\1 ", d["text"], flags=re.M)  # titres décalés d'un niveau
        out.append(f"## Document {i} : {d['name']}\n\n{body}")
    return "\n\n".join(out)


@router.post("/generate")
def generate(body: GenerateIn, request: Request):
    idea = body.idea.strip()
    if not idea:
        return JSONResponse({"error": "Saisissez une idée."}, 400)
    user_id = request.state.user["id"]
    with db() as con:
        ctx = ""
        if body.context_id:
            r = con.execute("SELECT text FROM contexts WHERE id=?", (body.context_id,)).fetchone()
            ctx = r["text"] if r else ""
        docs = []
        for doc_id in dict.fromkeys(body.doc_ids):
            r = con.execute("SELECT id, name, status, sections, tokens_full FROM documents WHERE id=?",
                            (doc_id,)).fetchone()
            if not r or r["status"] != "prêt":
                return JSONResponse({"error": f"Document {doc_id} absent ou pas encore prêt."}, 400)
            text = kept_text(json.loads(r["sections"] or "[]"))
            docs.append({"id": r["id"], "name": r["name"], "text": text, "full": r["tokens_full"]})
    total = sum(tokens(d["text"]) for d in docs)
    if total > DOC_BUDGET:
        return JSONResponse({"error": f"{total} tokens de documents : trop pour num_ctx "
                                      f"({DOC_BUDGET} disponibles). Retirez un document ou des sections."}, 400)
    message = build_message(idea, ctx, docs, body.attach)

    def stream():
        t0 = time.time()
        parts = []
        try:
            for chunk in ollama_stream(PROMPT_MODEL, [{"role": "user", "content": message}]):
                parts.append(chunk)
                yield ndjson({"t": chunk})
        except OllamaError as e:  # Ollama a répondu, mais refuse : on affiche sa raison
            yield ndjson({"error": ollama_message(e)})
            return
        except Exception as e:
            yield ndjson({"error": f"Prompteur injoignable : {e}"})
            return
        prompt = strip_fence("".join(parts))
        if body.attach and docs:
            prompt += "\n\n" + appendix(docs)
        duration = round(time.time() - t0, 1)
        before = sum(d["full"] for d in docs) + tokens(idea) + tokens(ctx)
        after = tokens(prompt)
        with db() as con:
            cur = con.execute(
                "INSERT INTO history(date, user_id, idea, context_id, attach, tokens_before, tokens_after, "
                "duration_s, model, prompt) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (now_iso(), user_id, idea, body.context_id, int(body.attach), before, after, duration,
                 PROMPT_MODEL, prompt))
            hid = cur.lastrowid
            con.executemany("INSERT INTO history_documents(history_id, document_id, document_name) VALUES (?,?,?)",
                            [(hid, d["id"], d["name"]) for d in docs])
        yield ndjson({"done": True, "id": hid, "prompt": prompt, "duration_s": duration,
                      "tokens_before": before, "tokens_after": after})

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def history_rows(con, where: str = "", args: tuple = ()):
    rows = con.execute(f"SELECT * FROM history {where} ORDER BY date DESC, id DESC LIMIT 500", args).fetchall()
    out = []
    for r in rows:
        docs = con.execute("SELECT document_id, document_name FROM history_documents WHERE history_id=?",
                           (r["id"],)).fetchall()
        out.append({
            "id": r["id"], "date": r["date"], "idea": r["idea"],
            "source": [d["document_name"] for d in docs], "doc_ids": [d["document_id"] for d in docs],
            "context_id": r["context_id"], "attach": bool(r["attach"]),
            "tokens_before": r["tokens_before"] or 0, "tokens_after": r["tokens_after"] or 0,
            "duration_s": r["duration_s"] or 0, "model": r["model"], "ideal": bool(r["ideal"]),
            "prompt": r["prompt"],
            "excerpt": (r["excerpt"] if "excerpt" in r.keys() else None) or "",
            "manual": bool(r["manual"]) if "manual" in r.keys() else False})
    return out


@router.get("/history")
def history():
    with db() as con:
        return history_rows(con)


@router.post("/history/{hid}/ideal")
def toggle_ideal(hid: int):
    with db() as con:
        r = con.execute("SELECT ideal FROM history WHERE id=?", (hid,)).fetchone()
        if not r:
            return JSONResponse({"error": "Entrée introuvable."}, 404)
        ideal = 0 if r["ideal"] else 1
        con.execute("UPDATE history SET ideal=? WHERE id=?", (ideal, hid))
    return {"ok": True, "ideal": bool(ideal)}


@router.delete("/history/{hid}")
def delete_history(hid: int):
    with db() as con:
        con.execute("DELETE FROM history WHERE id=?", (hid,))
    return {"ok": True}
