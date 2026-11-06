# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outils communs à tous les tests du Prompteur Claude.

Principe :
- chaque test reçoit un dossier de données vide (base SQLite neuve) : aucun test ne touche `data/` ;
- les modules de l'app lisent leurs réglages (DATA_DIR, APP_MODE…) à l'import : on les recharge
  pour chaque test, en mode « user » (site, port 8093) ou « admin » (tableau de bord, port 8094) ;
- Ollama, l'API Claude et nvidia-smi ne sont jamais appelés : des imitations les remplacent.
  Les tests « live » (tests/live/) visent, eux, le vrai serveur.
"""
import importlib
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Modules qui dépendent des variables d'environnement : rechargés à chaque test (ceux-ci, plus tout
# nouveau fichier .py de la racine). convert.py n'en dépend pas, il reste en cache (MarkItDown est long).
APP_MODULES = ["app", "admin", "auth", "chat", "legacy", "library", "prompts", "core"]

ADMIN_USER, ADMIN_PASSWORD = "admin", "admin-pass-123"
USER, USER_PASSWORD = "alice", "alice-pass-123"


def _set_env(monkeypatch, data_dir: Path, mode: str):
    env = {
        "DATA_DIR": str(data_dir),
        "APP_MODE": mode,
        "SECRET_KEY": "cle-de-test-uniquement",
        "ADMIN_USER": ADMIN_USER,
        "ADMIN_PASSWORD": ADMIN_PASSWORD,
        # Adresses volontairement invalides : un appel réseau oublié échoue tout de suite
        "OLLAMA_URL": "http://ollama.invalid:11434",
        "SITE_URL": "http://site.invalid:8000",
        "PROMPT_MODEL": "prompteur-claude",
        "FALLBACK_MODEL": "qwen3:4b-instruct",
        "GIT_COMMIT": "test",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in ("NUM_CTX", "HEALTH_URLS", "SERVER_NAME", "CONTEXT_FILE"):
        monkeypatch.delenv(k, raising=False)


def _app_module_names():
    """Tous les modules de l'app (fichiers .py à la racine du dépôt), sauf convert.py."""
    names = {p.stem for p in ROOT.glob("*.py")} - {"convert"}
    return [n for n in APP_MODULES if n in names] + sorted(names - set(APP_MODULES))


def _fresh_import(name: str):
    for m in _app_module_names():
        sys.modules.pop(m, None)
    return importlib.import_module(name)


# ---------- Module core seul (aucune dépendance à FastAPI) ----------
@pytest.fixture
def core(tmp_path, monkeypatch):
    _set_env(monkeypatch, tmp_path, "user")
    return _fresh_import("core")


# ---------- Application complète ----------
class Site:
    """Une instance de l'app, avec ses modules (pour les imitations) et un client HTTP."""

    def __init__(self, mode, modules, client_factory, original=None):
        self.mode = mode
        self.m = modules
        self.original = original or {}  # vraies fonctions, avant le filet de sécurité
        self._factory = client_factory

    def client(self):
        return self._factory()

    def login(self, username=ADMIN_USER, password=ADMIN_PASSWORD):
        c = self.client()
        r = c.post("/api/login", json={"username": username, "password": password})
        assert r.status_code == 200, f"connexion impossible : {r.status_code} {r.text}"
        return c

    def add_user(self, username=USER, password=USER_PASSWORD, is_admin=False):
        return self.m.core.create_user(username, password, is_admin)


def _build_site(tmp_path, monkeypatch, mode):
    pytest.importorskip("fastapi", reason="FastAPI absent : pip install -r requirements.txt")
    pytest.importorskip("httpx", reason="httpx absent : pip install -r requirements-dev.txt")
    from fastapi.testclient import TestClient

    _set_env(monkeypatch, tmp_path, mode)
    monkeypatch.chdir(ROOT)  # StaticFiles(directory="static") est relatif
    app_mod = _fresh_import("app")
    mods = types.SimpleNamespace(**{n: sys.modules[n] for n in _app_module_names() if n in sys.modules})
    mods.app = app_mod
    mods.convert = sys.modules["convert"]
    # Pas d'attente d'une seconde après un mauvais mot de passe
    monkeypatch.setattr(mods.auth, "time", types.SimpleNamespace(time=time.time, sleep=lambda s: None))
    original = {"claude_stream": mods.chat.claude_stream, "ollama_stream": mods.core.ollama_stream}
    # Filet de sécurité : aucun appel réel à Ollama ni à l'API Claude
    def no_network(*a, **k):
        raise AssertionError("appel réseau non imité dans un test")
    monkeypatch.setattr(mods.prompts, "ollama_stream", no_network)
    monkeypatch.setattr(mods.chat, "ollama_stream", no_network)
    monkeypatch.setattr(mods.chat, "claude_stream", no_network)
    if hasattr(mods, "admin"):
        monkeypatch.setattr(mods.admin, "ollama_json", no_network)
    if hasattr(mods, "agents"):
        original["agents_ollama_json"] = mods.agents.ollama_json
        monkeypatch.setattr(mods.agents, "ollama_stream", no_network)
        monkeypatch.setattr(mods.agents, "ollama_json", no_network)

    clients = []

    def factory():
        c = TestClient(app_mod.app, base_url="http://testserver", follow_redirects=False)
        clients.append(c)
        return c

    site = Site(mode, mods, factory, original)
    yield site
    for c in clients:
        c.close()


@pytest.fixture
def site(tmp_path, monkeypatch):
    """Site du Prompteur (APP_MODE=user, md-converter)."""
    yield from _build_site(tmp_path, monkeypatch, "user")


@pytest.fixture
def admin_site(tmp_path, monkeypatch):
    """Tableau de bord (APP_MODE=admin, md-admin), même base que le site."""
    yield from _build_site(tmp_path, monkeypatch, "admin")


@pytest.fixture
def client(site):
    """Client connecté avec le compte admin créé depuis .env."""
    return site.login()


# ---------- Aides ----------
def fake_stream(chunks, error=None):
    """Imitation d'ollama_stream / claude_stream : renvoie les morceaux, puis lève `error` si donnée."""
    calls = []

    def _stream(*args, **kwargs):
        calls.append(args)
        for c in chunks:
            yield c
        if error:
            raise error

    _stream.calls = calls
    return _stream


def ndjson_lines(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def add_ready_doc(site, name="doc.md", text="# Titre\n\nContenu du document.", tags=None):
    """Insère directement un document « prêt » dans la base, sans passer par la conversion."""
    m = site.m
    secs = m.convert.split_sections(text)
    with m.core.db() as con:
        cur = con.execute(
            "INSERT INTO documents(name, type, date, size, tokens, tokens_full, tags, status, markdown, sections) "
            "VALUES (?,?,?,?,?,?,?, 'prêt', ?, ?)",
            (name, "Markdown", m.core.now_iso(), len(text), m.core.tokens(text), m.core.tokens(text),
             json.dumps(tags or []), text, json.dumps(secs, ensure_ascii=False)))
        return cur.lastrowid
