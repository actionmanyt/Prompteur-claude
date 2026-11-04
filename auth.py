"""Connexion par identifiant et mot de passe (cookie de session)."""
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core import check_pw, db, hash_pw

COOKIE = "prompteur_session"
SESSION_DAYS = 30
router = APIRouter(prefix="/api")
_failures: dict[str, list[float]] = {}  # tentatives ratées par adresse IP


def user_from_token(token: str | None):
    if not token:
        return None
    with db() as con:
        r = con.execute(
            "SELECT u.id, u.username, u.is_admin FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ? AND s.expires > ?", (token, time.time())).fetchone()
    return dict(r) if r else None


class Login(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: Login, request: Request):
    ip = request.client.host if request.client else "?"
    recent = [t for t in _failures.get(ip, []) if t > time.time() - 600]
    if len(recent) >= 8:
        return JSONResponse({"error": "Trop de tentatives : réessayez dans 10 minutes."}, 429)
    with db() as con:
        r = con.execute("SELECT id, pw_hash FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
    if not r or not check_pw(body.password, r["pw_hash"]):
        _failures[ip] = recent + [time.time()]
        time.sleep(1)
        return JSONResponse({"error": "Identifiant ou mot de passe incorrect."}, 401)
    _failures.pop(ip, None)
    token = secrets.token_urlsafe(32)
    with db() as con:
        con.execute("INSERT INTO sessions(token, user_id, expires) VALUES (?,?,?)",
                    (token, r["id"], time.time() + SESSION_DAYS * 86400))
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", path="/")
    return resp


@router.post("/logout")
def logout(request: Request):
    with db() as con:
        con.execute("DELETE FROM sessions WHERE token = ?", (request.cookies.get(COOKIE, ""),))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/me")
def me(request: Request):
    u = request.state.user
    return {"username": u["username"], "is_admin": bool(u["is_admin"])}


class NewPassword(BaseModel):
    old: str
    new: str


@router.post("/me/password")
def change_password(body: NewPassword, request: Request):
    u = request.state.user
    if len(body.new) < 8:
        return JSONResponse({"error": "8 caractères minimum."}, 400)
    with db() as con:
        r = con.execute("SELECT pw_hash FROM users WHERE id = ?", (u["id"],)).fetchone()
        if not check_pw(body.old, r["pw_hash"]):
            return JSONResponse({"error": "Ancien mot de passe incorrect."}, 400)
        con.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_pw(body.new), u["id"]))
    return {"ok": True}
