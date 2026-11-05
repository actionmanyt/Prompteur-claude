# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bibliothèque de documents (envoi, conversion en tâche de fond) et contextes enregistrés."""
import json
import os
import tempfile
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from convert import NotSupported, clean, file_to_md, split_sections, type_label, url_to_md, youtube_id
from core import TMP_DIR, db, now_iso, tokens

router = APIRouter(prefix="/api")
UPLOAD_MAX = 50 * 1024 * 1024
LIST_COLS = "id, name, type, date, size, tokens, tokens_full, tags, status, error, source_url"


def summary(r) -> dict:
    d = {k: r[k] for k in r.keys() if k not in ("sections", "markdown")}
    d["tags"] = json.loads(r["tags"] or "[]")
    return d


def get_doc(con, doc_id: int):
    return con.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()


def kept_text(sections: list[dict]) -> str:
    return "\n\n".join(s["text"] for s in sections if s.get("on"))


def save_conversion(doc_id: int, text: str, cut_tail: bool):
    text = clean(text, cut_tail)
    if not text.strip():
        raise NotSupported("aucun texte trouvé dans ce fichier")
    secs = split_sections(text)
    with db() as con:
        con.execute("UPDATE documents SET status='prêt', error=NULL, markdown=?, sections=?, tokens=?, tokens_full=? "
                    "WHERE id=?", (text, json.dumps(secs, ensure_ascii=False), tokens(kept_text(secs)),
                                   tokens(text), doc_id))


def mark_failed(doc_id: int, e: Exception):
    status = "non pris en charge" if isinstance(e, NotSupported) else "erreur"
    with db() as con:
        con.execute("UPDATE documents SET status=?, error=? WHERE id=?", (status, str(e)[:400], doc_id))


def convert_file_job(doc_id: int, path: str, name: str):
    try:
        text, cut_tail = file_to_md(path, name)
        save_conversion(doc_id, text, cut_tail)
    except Exception as e:
        mark_failed(doc_id, e)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def convert_url_job(doc_id: int, url: str):
    try:
        text, kind = url_to_md(url)
        with db() as con:
            con.execute("UPDATE documents SET type=? WHERE id=?", (kind, doc_id))
            if kind == "Vidéo YouTube":  # le titre de la vidéo remplace l'adresse
                con.execute("UPDATE documents SET name=? WHERE id=?", (text.split("\n", 1)[0][2:122], doc_id))
        save_conversion(doc_id, text, True)
    except Exception as e:
        mark_failed(doc_id, e)


@router.get("/library")
def list_docs():
    with db() as con:
        rows = con.execute(f"SELECT {LIST_COLS} FROM documents ORDER BY date DESC, id DESC").fetchall()
    return [summary(r) for r in rows]


@router.post("/library")
async def upload(bg: BackgroundTasks, files: list[UploadFile] = File(...)):
    out = []
    for f in files:
        name = os.path.basename(f.filename or "sans-nom")
        fd, path = tempfile.mkstemp(suffix=os.path.splitext(name)[1], dir=TMP_DIR)
        size, too_big = 0, False
        with os.fdopen(fd, "wb") as w:
            while chunk := await f.read(1 << 20):
                size += len(chunk)
                if size > UPLOAD_MAX:
                    too_big = True
                    break
                w.write(chunk)
        if too_big:
            os.unlink(path)
            out.append({"name": name, "error": "trop volumineux (50 Mo au maximum)"})
            continue
        with db() as con:
            cur = con.execute("INSERT INTO documents(name, type, date, size, status) VALUES (?,?,?,?, 'en cours')",
                              (name, type_label(name), now_iso(), size))
            doc_id = cur.lastrowid
        bg.add_task(convert_file_job, doc_id, path, name)
        out.append({"id": doc_id, "name": name})
    return out


class UrlIn(BaseModel):
    url: str


@router.post("/library/url")
def add_url(body: UrlIn, bg: BackgroundTasks):
    url = body.url.strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return JSONResponse({"error": "Adresse invalide (elle doit commencer par https://)."}, 400)
    name = (p.netloc + p.path).rstrip("/")[:120]
    kind = "Page web"
    if video_id := youtube_id(url):
        name, kind = f"YouTube {video_id}", "Vidéo YouTube"
    with db() as con:
        cur = con.execute("INSERT INTO documents(name, type, date, size, status, source_url) "
                          "VALUES (?, ?, ?, 0, 'en cours', ?)", (name, kind, now_iso(), url))
        doc_id = cur.lastrowid
    bg.add_task(convert_url_job, doc_id, url)
    return {"id": doc_id, "name": name}


@router.get("/library/{doc_id}")
def doc_detail(doc_id: int):
    with db() as con:
        r = get_doc(con, doc_id)
    if not r:
        return JSONResponse({"error": "Document introuvable."}, 404)
    d = summary(r)
    d["sections"] = [{**s, "tokens": tokens(s["text"])} for s in json.loads(r["sections"] or "[]")]
    return d


class DocPatch(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    sections_on: list[bool] | None = None


@router.patch("/library/{doc_id}")
def update_doc(doc_id: int, body: DocPatch):
    with db() as con:
        r = get_doc(con, doc_id)
        if not r:
            return JSONResponse({"error": "Document introuvable."}, 404)
        if body.name is not None and body.name.strip():
            con.execute("UPDATE documents SET name=? WHERE id=?", (body.name.strip()[:200], doc_id))
        if body.tags is not None:
            tags = sorted({t.strip().lower() for t in body.tags if t.strip()})[:20]
            con.execute("UPDATE documents SET tags=? WHERE id=?", (json.dumps(tags, ensure_ascii=False), doc_id))
        if body.sections_on is not None:
            secs = json.loads(r["sections"] or "[]")
            if len(body.sections_on) != len(secs):
                return JSONResponse({"error": "Nombre de sections incorrect."}, 400)
            for s, on in zip(secs, body.sections_on):
                s["on"] = on
            con.execute("UPDATE documents SET sections=?, tokens=? WHERE id=?",
                        (json.dumps(secs, ensure_ascii=False), tokens(kept_text(secs)), doc_id))
        r = con.execute(f"SELECT {LIST_COLS} FROM documents WHERE id=?", (doc_id,)).fetchone()
    return summary(r)


@router.delete("/library/{doc_id}")
def delete_doc(doc_id: int):
    with db() as con:
        con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    return {"ok": True}


# ---------- Contextes ----------
class ContextIn(BaseModel):
    name: str
    text: str


@router.get("/contexts")
def list_contexts():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT id, name, text FROM contexts ORDER BY name")]


@router.post("/contexts")
def add_context(body: ContextIn):
    if not body.name.strip():
        return JSONResponse({"error": "Donnez un nom au contexte."}, 400)
    with db() as con:
        cur = con.execute("INSERT INTO contexts(name, text) VALUES (?,?)", (body.name.strip(), body.text.strip()))
    return {"id": cur.lastrowid, "name": body.name.strip(), "text": body.text.strip()}


@router.put("/contexts/{ctx_id}")
def edit_context(ctx_id: int, body: ContextIn):
    if not body.name.strip():
        return JSONResponse({"error": "Donnez un nom au contexte."}, 400)
    with db() as con:
        con.execute("UPDATE contexts SET name=?, text=? WHERE id=?", (body.name.strip(), body.text.strip(), ctx_id))
    return {"id": ctx_id, "name": body.name.strip(), "text": body.text.strip()}


@router.delete("/contexts/{ctx_id}")
def delete_context(ctx_id: int):
    with db() as con:
        con.execute("DELETE FROM contexts WHERE id=?", (ctx_id,))
    return {"ok": True}
