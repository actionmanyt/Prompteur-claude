"""Prompteur Claude : point d'entrée FastAPI."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import admin
import auth
import chat
import legacy
import library
import prompts
from core import init_db

init_db()
app = FastAPI(title="Prompteur Claude", docs_url=None, redoc_url=None, openapi_url=None)

PUBLIC = {"/login.html", "/style.css", "/common.js", "/api/login", "/favicon.ico"}
ADMIN_ONLY = ("/api/admin", "/admin.html")


@app.middleware("http")
async def guard(request: Request, call_next):
    """Toutes les pages et routes demandent d'être connecté, sauf la page de connexion."""
    path = request.url.path
    if path in PUBLIC:
        return await call_next(request)
    user = auth.user_from_token(request.cookies.get(auth.COOKIE))
    if not user:
        if path.startswith("/api/"):
            return JSONResponse({"error": "Non connecté."}, 401)
        return RedirectResponse("/login.html", 303)
    if path.startswith(ADMIN_ONLY) and not user["is_admin"]:
        if path.startswith("/api/"):
            return JSONResponse({"error": "Réservé à l'administrateur."}, 403)
        return RedirectResponse("/", 303)
    request.state.user = user
    return await call_next(request)


for r in (auth.router, library.router, prompts.router, admin.router, chat.router, legacy.router):
    app.include_router(r)


@app.get("/test")
@app.get("/test/{rest:path}")
def old_test_pages(rest: str = ""):
    return RedirectResponse("/", 301)


# Les pages (index, admin, chat, login) : à monter en dernier
app.mount("/", StaticFiles(directory="static", html=True), name="pages")
