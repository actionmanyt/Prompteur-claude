# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests « live » : vérifient l'installation réelle (srv), après chaque ./deploy.sh.

Ils n'utilisent que la bibliothèque standard et ne modifient rien, sauf la génération réelle
(désactivée par défaut, car elle ajoute une ligne à l'historique).

Variables d'environnement :
    PROMPTEUR_URL           site, ex. http://100.101.4.16:8093  (sans elle, tout est ignoré)
    PROMPTEUR_ADMIN_URL     tableau de bord, ex. http://100.101.4.16:8094
    PROMPTEUR_USER / PROMPTEUR_PASSWORD   compte de test (admin pour le tableau de bord)
    PROMPTEUR_LIVE_GENERATE=1   lance une vraie génération avec Ollama
    PROMPTEUR_MAX_SECONDS   durée maximale d'une génération (30 par défaut)
"""
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.live

SITE = os.environ.get("PROMPTEUR_URL", "").rstrip("/")
ADMIN = os.environ.get("PROMPTEUR_ADMIN_URL", "").rstrip("/")
USER = os.environ.get("PROMPTEUR_USER", "")
PASSWORD = os.environ.get("PROMPTEUR_PASSWORD", "")
MAX_SECONDS = float(os.environ.get("PROMPTEUR_MAX_SECONDS", "30"))

if not SITE:
    pytest.skip("PROMPTEUR_URL non renseignée : tests live ignorés", allow_module_level=True)


class Http:
    """Petit client HTTP avec cookies, qui renvoie (code, en-têtes, corps) sans lever d'exception."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())

    def call(self, method, path, body=None, timeout=10):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            pytest.fail(f"{self.base}{path} injoignable : {e}. Le conteneur est-il Up ? Tailscale connecté ?")

    def json(self, method, path, body=None, timeout=10):
        code, _, text = self.call(method, path, body, timeout)
        try:
            return code, json.loads(text)
        except json.JSONDecodeError:
            pytest.fail(f"{path} : réponse non JSON (HTTP {code}) : {text[:200]}")

    def login(self):
        if not (USER and PASSWORD):
            pytest.skip("PROMPTEUR_USER / PROMPTEUR_PASSWORD non renseignés")
        code, j = self.json("POST", "/api/login", {"username": USER, "password": PASSWORD})
        assert code == 200, f"connexion refusée ({code}) : {j}"
        return self


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@pytest.fixture(scope="module")
def site():
    return Http(SITE).login()


# ---------- Site (8093) ----------
def test_site_repond():
    code, _, html = Http(SITE).call("GET", "/login.html")
    assert code == 200 and "Code source" in html


def test_site_protege_sans_connexion():
    assert Http(SITE).call("GET", "/api/library")[0] == 401


def test_site_cache_l_admin():
    assert Http(SITE).call("GET", "/api/admin/health")[0] == 404


def test_site_mode_user():
    assert Http(SITE).json("GET", "/api/site")[1] == {"mode": "user"}


def test_bibliotheque_et_contextes(site):
    code, docs = site.json("GET", "/api/library")
    assert code == 200 and isinstance(docs, list)
    code, ctx = site.json("GET", "/api/contexts")
    assert code == 200 and any(c["name"] == "Mon contexte" for c in ctx)


def test_aucun_document_bloque_en_cours(site):
    """Un document « en cours » depuis plus de 15 min signale une conversion plantée."""
    from datetime import datetime, timezone
    _, docs = site.json("GET", "/api/library")
    now = datetime.now(timezone.utc)
    stuck = [d["name"] for d in docs if d["status"] == "en cours"
             and (now - datetime.fromisoformat(d["date"])).total_seconds() > 900]
    assert not stuck, f"conversions bloquées : {stuck}"


def test_idee_vide_refusee(site):
    assert site.json("POST", "/api/generate", {"idea": " "})[0] == 400


@pytest.mark.skipif(os.environ.get("PROMPTEUR_LIVE_GENERATE") != "1",
                    reason="PROMPTEUR_LIVE_GENERATE=1 pour lancer une vraie génération")
def test_generation_reelle_reactive(site):
    """Objectif du projet : le prompt s'affiche en quelques secondes, avec les 4 sections."""
    t0 = time.time()
    code, _, text = site.call("POST", "/api/generate",
                              {"idea": "[test automatique] Écris une liste de courses pour la semaine"},
                              timeout=MAX_SECONDS + 30)
    duree = time.time() - t0
    assert code == 200, text[:300]
    lines = [json.loads(x) for x in text.splitlines() if x.strip()]
    assert "error" not in lines[-1], lines[-1]
    prompt = lines[-1]["prompt"]
    manquantes = [s for s in ("Contexte", "Tâche", "Contraintes", "Format") if s not in prompt]
    assert not manquantes, f"sections absentes du prompt : {manquantes}\n{prompt}"
    assert duree < MAX_SECONDS, f"génération trop lente : {duree:.1f} s (Ollama sur le CPU ?)"


# ---------- Tableau de bord (8094) ----------
@pytest.fixture(scope="module")
def admin():
    if not ADMIN:
        pytest.skip("PROMPTEUR_ADMIN_URL non renseignée")
    return Http(ADMIN).login()


def test_tableau_de_bord_sante(admin):
    code, h = admin.json("GET", "/api/admin/health", timeout=20)
    assert code == 200
    ko = [f"{s['name']} : {s['detail']}" for s in h["services"] if s["state"] != "Up"]
    assert not ko, "services en erreur :\n" + "\n".join(ko)


def test_tableau_de_bord_gpu(admin):
    code, g = admin.json("GET", "/api/admin/gpu")
    assert code == 200, f"GPU illisible : {g}. Vérifier « docker exec md-admin nvidia-smi »"
    assert "1060" in g["name"]
    assert g["mem_used"] < g["mem_total"]


def test_modele_prompteur_present(admin):
    code, h = admin.json("GET", "/api/admin/health", timeout=20)
    modele = next(s for s in h["services"] if s["name"].startswith("modèle "))
    assert modele["state"] == "Up", modele["detail"]
    assert "qwen3" in modele["detail"].lower(), f"modèle de base inattendu : {modele['detail']}"
