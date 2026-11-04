# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gestion des comptes depuis le tableau de bord (port 8094, comptes admin uniquement).

Règles :
- l'identifiant : 2 à 32 caractères, lettres, chiffres, point, tiret, tiret bas ;
- le mot de passe donné par l'admin est provisoire par défaut : l'utilisateur doit le changer à sa connexion ;
- un nouveau mot de passe ferme toutes les sessions du compte ;
- on ne peut ni se supprimer soi-même, ni retirer ses propres droits, ni supprimer le dernier admin.
"""
import sqlite3
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core import USERNAME_RE, create_user, db, hash_pw, password_problem

router = APIRouter(prefix="/api/admin/users")


def err(msg: str, code: int = 400):
    return JSONResponse({"error": msg}, code)


def user_row(con, uid: int):
    return con.execute("SELECT id, username, is_admin FROM users WHERE id=?", (uid,)).fetchone()


def admin_count(con) -> int:
    return con.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0]


@router.get("")
def list_users():
    now = time.time()
    with db() as con:
        rows = con.execute(
            "SELECT u.id, u.username, u.is_admin, u.must_change, u.created, "
            "(SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id AND s.expires > ?) AS sessions, "
            "(SELECT MAX(date) FROM history h WHERE h.user_id=u.id) AS last_prompt "
            "FROM users u ORDER BY u.username COLLATE NOCASE", (now,)).fetchall()
    return [{**dict(r), "is_admin": bool(r["is_admin"]), "must_change": bool(r["must_change"])} for r in rows]


class NewUser(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    temporary: bool = True  # l'utilisateur devra changer ce mot de passe à sa connexion


@router.post("")
def add_user(body: NewUser):
    name = body.username.strip()
    if not USERNAME_RE.match(name):
        return err("Identifiant : 2 à 32 caractères (lettres, chiffres, . - _), sans espace ni accent.")
    problem = password_problem(body.password)
    if problem:
        return err("Mot de passe : " + problem)
    with db() as con:
        taken = con.execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (name,)).fetchone()
    if taken:
        return err(f"Le compte « {name} » existe déjà.", 409)
    try:
        uid = create_user(name, body.password, body.is_admin, body.temporary)
    except sqlite3.IntegrityError:
        return err(f"Le compte « {name} » existe déjà.", 409)
    return {"ok": True, "id": uid, "username": name}


class NewPassword(BaseModel):
    password: str
    temporary: bool = True


@router.post("/{uid}/password")
def reset_password(uid: int, body: NewPassword, request: Request):
    problem = password_problem(body.password)
    if problem:
        return err("Mot de passe : " + problem)
    me = request.state.user
    with db() as con:
        if not user_row(con, uid):
            return err("Compte introuvable.", 404)
        # Pour son propre compte, pas de mot de passe « provisoire » qui bloquerait sa propre session
        temporary = body.temporary and uid != me["id"]
        con.execute("UPDATE users SET pw_hash=?, must_change=? WHERE id=?",
                    (hash_pw(body.password), int(temporary), uid))
        con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    return {"ok": True, "self": uid == me["id"]}


class Role(BaseModel):
    is_admin: bool


@router.patch("/{uid}")
def set_role(uid: int, body: Role, request: Request):
    with db() as con:
        u = user_row(con, uid)
        if not u:
            return err("Compte introuvable.", 404)
        if not body.is_admin and u["is_admin"]:
            if uid == request.state.user["id"]:
                return err("Vous ne pouvez pas retirer vos propres droits d'administrateur.")
            if admin_count(con) <= 1:
                return err("Il faut garder au moins un administrateur.")
        con.execute("UPDATE users SET is_admin=? WHERE id=?", (int(body.is_admin), uid))
    return {"ok": True, "is_admin": body.is_admin}


@router.post("/{uid}/logout")
def close_sessions(uid: int):
    with db() as con:
        if not user_row(con, uid):
            return err("Compte introuvable.", 404)
        n = con.execute("DELETE FROM sessions WHERE user_id=?", (uid,)).rowcount
    return {"ok": True, "closed": n}


@router.delete("/{uid}")
def delete_user(uid: int, request: Request):
    with db() as con:
        u = user_row(con, uid)
        if not u:
            return err("Compte introuvable.", 404)
        if uid == request.state.user["id"]:
            return err("Vous ne pouvez pas supprimer votre propre compte.")
        if u["is_admin"] and admin_count(con) <= 1:
            return err("Il faut garder au moins un administrateur.")
        # Sessions, clé API et réglages partent avec le compte (ON DELETE CASCADE) ;
        # l'historique et le compteur de coût sont gardés, sans lien vers le compte.
        con.execute("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}
