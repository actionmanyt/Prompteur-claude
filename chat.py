# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tchat Claude : clé API de l'utilisateur (chiffrée), plafond mensuel, Ollama en secours."""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core import FALLBACK_MODEL, db, decrypt, encrypt, ndjson, now_iso, ollama_stream, tokens

router = APIRouter(prefix="/api/chat")
MODELS = {
    "haiku": os.environ.get("CLAUDE_HAIKU", "claude-haiku-5-5"),
    "sonnet": os.environ.get("CLAUDE_SONNET", "claude-sonnet-5-5"),
}
# $ par million de tokens (entrée, sortie) : à vérifier sur la page des tarifs d'Anthropic
DEFAULT_PRICES = {"haiku": {"in": 1.0, "out": 5.0}, "sonnet": {"in": 3.0, "out": 15.0}}
API_URL = "https://api.anthropic.com/v1/messages"


def settings_of(con, user_id: int) -> dict:
    r = con.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    if not r:
        return {"api_key_enc": None, "cap": 5.0, "default_model": "haiku", "prices": DEFAULT_PRICES}
    return {"api_key_enc": r["api_key_enc"], "cap": r["cap"] or 0.0, "default_model": r["default_model"] or "haiku",
            "prices": json.loads(r["prices"]) if r["prices"] else DEFAULT_PRICES}


def month_usage(con, user_id: int) -> tuple[float, int]:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    r = con.execute("SELECT COALESCE(SUM(cost),0), COALESCE(SUM(tokens_in+tokens_out),0) FROM chat_usage "
                    "WHERE user_id=? AND date LIKE ?", (user_id, month + "%")).fetchone()
    return float(r[0]), int(r[1])


@router.get("/settings")
def get_settings(request: Request):
    with db() as con:
        s = settings_of(con, request.state.user["id"])
    hint = ""
    if s["api_key_enc"]:
        try:
            hint = "…" + decrypt(s["api_key_enc"])[-4:]
        except Exception:
            hint = "illisible (SECRET_KEY changée ?)"
    return {"has_key": bool(s["api_key_enc"]), "key_hint": hint, "cap": s["cap"],
            "default_model": s["default_model"], "prices": s["prices"], "models": MODELS}


class SettingsIn(BaseModel):
    api_key: str | None = None
    delete_key: bool = False
    cap: float = 5.0
    default_model: str = "haiku"
    prices: dict | None = None


@router.post("/settings")
def save_settings(body: SettingsIn, request: Request):
    uid = request.state.user["id"]
    if body.default_model not in MODELS:
        return JSONResponse({"error": "Modèle inconnu."}, 400)
    with db() as con:
        s = settings_of(con, uid)
        enc = s["api_key_enc"]
        if body.delete_key:
            enc = None
        elif body.api_key and body.api_key.strip():
            enc = encrypt(body.api_key.strip())
        prices = body.prices or s["prices"]
        con.execute("INSERT INTO user_settings(user_id, api_key_enc, cap, default_model, prices) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET api_key_enc=excluded.api_key_enc, cap=excluded.cap, "
                    "default_model=excluded.default_model, prices=excluded.prices",
                    (uid, enc, max(0.0, body.cap), body.default_model, json.dumps(prices)))
    return get_settings(request)


@router.get("/usage")
def usage(request: Request):
    with db() as con:
        cost, tok = month_usage(con, request.state.user["id"])
        cap = settings_of(con, request.state.user["id"])["cap"]
    return {"month_cost": cost, "month_tokens": tok, "cap": cap}


class ChatIn(BaseModel):
    model: str = "haiku"
    messages: list[dict]


def claude_stream(key: str, model_id: str, messages: list, state: dict):
    payload = json.dumps({"model": model_id, "max_tokens": 4096, "stream": True, "messages": messages}).encode()
    req = urllib.request.Request(API_URL, data=payload, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            t = ev.get("type")
            if t == "message_start":
                state["tin"] = ev["message"].get("usage", {}).get("input_tokens", 0)
            elif t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                yield ev["delta"]["text"]
            elif t == "message_delta":
                state["tout"] = ev.get("usage", {}).get("output_tokens", state.get("tout", 0))
            elif t == "error":
                raise RuntimeError(ev.get("error", {}).get("message", "erreur de l'API"))


def api_error(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        if e.code == 401:
            return "clé API refusée"
        return f"HTTP {e.code} {msg}".strip()
    return str(e) or type(e).__name__


@router.post("")
def chat(body: ChatIn, request: Request):
    uid = request.state.user["id"]
    label = body.model if body.model in MODELS else "haiku"
    msgs = [{"role": m.get("role"), "content": str(m.get("content", "")).strip()} for m in body.messages]
    msgs = [m for m in msgs if m["role"] in ("user", "assistant") and m["content"]]
    if not msgs or msgs[-1]["role"] != "user":
        return JSONResponse({"error": "Le dernier message doit venir de vous."}, 400)
    with db() as con:
        s = settings_of(con, uid)
        cost_month, _ = month_usage(con, uid)
    if s["cap"] and cost_month >= s["cap"]:
        return JSONResponse({"error": f"Plafond mensuel atteint ({s['cap']} $) : envoi bloqué."}, 402)
    key = None
    if s["api_key_enc"]:
        try:
            key = decrypt(s["api_key_enc"])
        except Exception:
            key = None
    price = s["prices"].get(label, DEFAULT_PRICES[label])

    def stream():
        state, got_text, reason = {"tin": 0, "tout": 0}, False, ""
        if key:
            try:
                for chunk in claude_stream(key, MODELS[label], msgs, state):
                    got_text = True
                    yield ndjson({"t": chunk})
            except Exception as e:
                reason = api_error(e)
                if got_text:
                    yield ndjson({"error": f"Réponse coupée : {reason}"})
        else:
            reason = "aucune clé API enregistrée"
        fallback = not got_text
        if fallback:
            yield ndjson({"t": f"(Réponse de secours d'Ollama / {FALLBACK_MODEL} : {reason}.)\n\n"})
            out = []
            try:
                for chunk in ollama_stream(FALLBACK_MODEL, msgs):
                    out.append(chunk)
                    yield ndjson({"t": chunk})
            except Exception as e:
                yield ndjson({"error": f"Ollama injoignable aussi : {e}"})
            tin, tout, cost = sum(tokens(m["content"]) for m in msgs), tokens("".join(out)), 0.0
        else:
            tin, tout = state["tin"], state["tout"]
            cost = (tin * float(price["in"]) + tout * float(price["out"])) / 1e6
            with db() as con:
                con.execute("INSERT INTO chat_usage(date, user_id, model, tokens_in, tokens_out, cost) "
                            "VALUES (?,?,?,?,?,?)", (now_iso(), uid, MODELS[label], tin, tout, cost))
        with db() as con:
            month_cost, month_tok = month_usage(con, uid)
        yield ndjson({"done": True, "model": FALLBACK_MODEL if fallback else label, "fallback": fallback,
                      "reason": reason, "tokens_in": tin, "tokens_out": tout, "cost": cost,
                      "month_cost": month_cost, "month_tokens": month_tok})

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
