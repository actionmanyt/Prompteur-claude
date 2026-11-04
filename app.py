# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prompteur Claude : point d'entrée FastAPI.

Le même code tourne dans deux conteneurs :
- APP_MODE=user  (md-converter, port 8093) : Utilisation et Tchat, sans aucun accès à l'Admin ;
- APP_MODE=admin (md-admin, port 8094)     : tableau de bord, réservé aux comptes administrateurs.
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import accounts
import admin
import auth
import chat
import legacy
import library
import prompts
from core import APP_MODE, init_db

init_db()
ADMIN_SITE = APP_MODE == "admin"
app = FastAPI(title="Prompteur Claude", docs_url=None, redoc_url=None, openapi_url=None)

PUBLIC = {"/login.html", "/style.css", "/common.js", "/api/login", "/api/logout", "/favicon.ico"}
# Avec un mot de passe provisoire, seules ces routes répondent tant qu'il n'est pas changé
MUST_CHANGE_OK = {"/api/me", "/api/me/password", "/api/logout", "/api/site"}
USER_PAGES = {"/", "/index.html", "/chat.html", "/ancien"}


@app.middleware("http")
async def guard(request: Request, call_next):
    """Toutes les pages et routes demandent d'être connecté, sauf la page de connexion."""
    path = request.url.path
    is_admin_path = path.startswith("/api/admin") or path == "/admin.html"
    # Le site du Prompteur ne connaît pas l'Admin
    if not ADMIN_SITE and is_admin_path:
        return PlainTextResponse("Page introuvable.", 404)
    if path in PUBLIC:
        return await call_next(request)
    user = auth.user_from_token(request.cookies.get(auth.COOKIE))
    if not user:
        if path.startswith("/api/"):
            return JSONResponse({"error": "Non connecté."}, 401)
        return RedirectResponse("/login.html", 303)
    if ADMIN_SITE:
        if not user["is_admin"]:
            if path.startswith("/api/"):
                return JSONResponse({"error": "Réservé à l'administrateur."}, 403)
            return RedirectResponse("/login.html?refus=1", 303)
        if path in USER_PAGES:
            return RedirectResponse("/admin.html", 303)
    if user["must_change"] and path.startswith("/api/") and path not in MUST_CHANGE_OK:
        return JSONResponse({"error": "Changez d'abord votre mot de passe provisoire.", "must_change": True}, 403)
    request.state.user = user
    return await call_next(request)


@app.get("/api/site", include_in_schema=False)
def site_info():
    return {"mode": APP_MODE}


PUBLIC.add("/api/site")

routers = [auth.router, library.router, prompts.router, chat.router]
routers += [admin.router, accounts.router] if ADMIN_SITE else [legacy.router]
for r in routers:
    app.include_router(r)


@app.get("/test")
@app.get("/test/{rest:path}")
def old_test_pages(rest: str = ""):
    return RedirectResponse("/", 301)


# Les pages (index, admin, chat, login) : à monter en dernier
app.mount("/", StaticFiles(directory="static", html=True), name="pages")
