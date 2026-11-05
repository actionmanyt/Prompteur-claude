# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prompts idéaux ajoutés à la main depuis le tableau de bord (port 8094, comptes admin).

Deux usages :
- écrire un prompt idéal de zéro (idée, extrait du document, prompt) ;
- corriger un prompt généré (from_id) : la correction devient une nouvelle entrée, l'original reste intact.

Les entrées manuelles vont dans la table `history` (model = « manuel », ideal = 1), donc dans
l'historique et dans l'export dataset.jsonl comme les autres.
"""
import sqlite3
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core import DOC_BUDGET, db, now_iso, tokens

router = APIRouter(prefix="/api/admin/ideal")
MANUAL_MODEL = "manuel"
MAX_IDEA = 2000      # caractères
MAX_PROMPT = 20000   # caractères


# ---------- Base ----------
def _add_column(con, sql: str):
    try:
        con.execute(sql)
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e):  # l'autre conteneur l'a ajoutée entre-temps
            raise


def migrate_db():
    """0.8.0 : colonnes `excerpt` (extrait saisi à la main) et `manual` de la table history.
    Lancé par les deux conteneurs au démarrage ; chaque étape supporte d'être faite deux fois."""
    for attempt in range(30):
        try:
            with db() as con:
                cols = {r[1] for r in con.execute("PRAGMA table_info(history)")}
                if "excerpt" not in cols:
                    _add_column(con, "ALTER TABLE history ADD COLUMN excerpt TEXT")
                if "manual" not in cols:
                    _add_column(con, "ALTER TABLE history ADD COLUMN manual INTEGER NOT NULL DEFAULT 0")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == 29:
                raise
            time.sleep(1)


# ---------- Routes ----------
class IdealIn(BaseModel):
    idea: str
    prompt: str
    excerpt: str = ""
    from_id: int | None = None  # entrée générée à corriger (ses documents liés sont repris)


def problem(body: IdealIn) -> str | None:
    idea, prompt = body.idea.strip(), body.prompt.strip()
    if not idea:
        return "Saisissez l'idée de départ."
    if not prompt:
        return "Saisissez le prompt idéal."
    if len(idea) > MAX_IDEA:
        return f"Idée trop longue ({len(idea)} caractères, {MAX_IDEA} au maximum)."
    if len(prompt) > MAX_PROMPT:
        return f"Prompt trop long ({len(prompt)} caractères, {MAX_PROMPT} au maximum)."
    if tokens(body.excerpt) > DOC_BUDGET:
        return f"Extrait trop long ({tokens(body.excerpt)} tokens, {DOC_BUDGET} au maximum) : gardez l'essentiel."
    return None


def counts(idea: str, excerpt: str, prompt: str) -> tuple[int, int]:
    return tokens(idea) + tokens(excerpt), tokens(prompt)


@router.post("")
def add_ideal(body: IdealIn, request: Request):
    err = problem(body)
    if err:
        return JSONResponse({"error": err}, 400)
    idea, prompt, excerpt = body.idea.strip(), body.prompt.strip(), body.excerpt.strip()
    before, after = counts(idea, excerpt, prompt)
    with db() as con:
        context_id, docs = None, []
        if body.from_id is not None:
            src = con.execute("SELECT id, context_id FROM history WHERE id=?", (body.from_id,)).fetchone()
            if not src:
                return JSONResponse({"error": "Entrée d'origine introuvable."}, 404)
            context_id = src["context_id"]
            docs = con.execute("SELECT document_id, document_name FROM history_documents WHERE history_id=?",
                               (src["id"],)).fetchall()
        hid = con.execute(
            "INSERT INTO history(date, user_id, idea, context_id, attach, tokens_before, tokens_after, duration_s, "
            "model, ideal, prompt, excerpt, manual) VALUES (?,?,?,?,0,?,?,0,?,1,?,?,1)",
            (now_iso(), request.state.user["id"], idea, context_id, before, after, MANUAL_MODEL, prompt,
             excerpt or None)).lastrowid
        con.executemany("INSERT INTO history_documents(history_id, document_id, document_name) VALUES (?,?,?)",
                        [(hid, d["document_id"], d["document_name"]) for d in docs])
    return {"ok": True, "id": hid}


@router.put("/{hid}")
def edit_ideal(hid: int, body: IdealIn):
    err = problem(body)
    if err:
        return JSONResponse({"error": err}, 400)
    idea, prompt, excerpt = body.idea.strip(), body.prompt.strip(), body.excerpt.strip()
    before, after = counts(idea, excerpt, prompt)
    with db() as con:
        r = con.execute("SELECT manual FROM history WHERE id=?", (hid,)).fetchone()
        if not r:
            return JSONResponse({"error": "Entrée introuvable."}, 404)
        if not r["manual"]:
            return JSONResponse({"error": "Un prompt généré ne se modifie pas : utilisez « Corriger en prompt idéal »."},
                                400)
        con.execute("UPDATE history SET idea=?, prompt=?, excerpt=?, tokens_before=?, tokens_after=? WHERE id=?",
                    (idea, prompt, excerpt or None, before, after, hid))
    return {"ok": True, "id": hid}
