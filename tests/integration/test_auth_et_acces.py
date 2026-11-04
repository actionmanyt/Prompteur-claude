# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests d'intégration : connexion, sessions, et séparation site (8093) / tableau de bord (8094)."""
from conftest import ADMIN_PASSWORD, ADMIN_USER, USER, USER_PASSWORD


# ---------- Pages publiques et accès refusé ----------
def test_page_de_connexion_publique(site):
    c = site.client()
    assert c.get("/login.html").status_code == 200
    assert c.get("/style.css").status_code == 200
    assert c.get("/api/site").json() == {"mode": "user"}


def test_api_sans_connexion_refusee(site):
    r = site.client().get("/api/library")
    assert r.status_code == 401
    assert r.json() == {"error": "Non connecté."}


def test_page_sans_connexion_renvoie_vers_login(site):
    r = site.client().get("/index.html")
    assert r.status_code == 303
    assert r.headers["location"] == "/login.html"


def test_pied_de_page_lien_code_source(client):
    """Obligation AGPL : chaque page doit proposer le code source."""
    for page in ("/login.html", "/index.html", "/chat.html"):
        html = client.get(page).text
        assert "github.com/actionmanyt/Prompteur-claude" in html, f"lien « Code source » absent de {page}"


# ---------- Connexion ----------
def test_connexion_et_profil(site):
    c = site.login()
    assert c.get("/api/me").json() == {"username": ADMIN_USER, "is_admin": True, "must_change": False}


def test_cookie_de_session_protege(site):
    r = site.client().post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_mauvais_mot_de_passe(site):
    r = site.client().post("/api/login", json={"username": ADMIN_USER, "password": "faux"})
    assert r.status_code == 401
    assert "set-cookie" not in r.headers


def test_compte_inconnu(site):
    r = site.client().post("/api/login", json={"username": "personne", "password": "x"})
    assert r.status_code == 401


def test_limite_des_tentatives(site):
    c = site.client()
    for _ in range(8):
        assert c.post("/api/login", json={"username": ADMIN_USER, "password": "faux"}).status_code == 401
    r = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert r.status_code == 429, "après 8 échecs, même le bon mot de passe doit être bloqué 10 minutes"


def test_deconnexion(site):
    c = site.login()
    token = c.cookies.get("prompteur_session")
    assert c.post("/api/logout").status_code == 200
    # Le jeton doit être effacé de la base, pas seulement du navigateur
    c.cookies.clear()
    c.cookies.set("prompteur_session", token)
    assert c.get("/api/me").status_code == 401


def test_jeton_invalide(site):
    c = site.client()
    c.cookies.set("prompteur_session", "jeton-invente")
    assert c.get("/api/me").status_code == 401


# ---------- Changement de mot de passe ----------
def test_changer_mot_de_passe(site):
    site.add_user()
    c = site.login(USER, USER_PASSWORD)
    assert c.post("/api/me/password", json={"old": USER_PASSWORD, "new": "court"}).status_code == 400
    assert c.post("/api/me/password", json={"old": "faux", "new": "nouveau-123"}).status_code == 400
    assert c.post("/api/me/password", json={"old": USER_PASSWORD, "new": USER_PASSWORD}).status_code == 400
    assert c.post("/api/me/password", json={"old": USER_PASSWORD, "new": " espace-123"}).status_code == 400
    assert c.post("/api/me/password", json={"old": USER_PASSWORD, "new": "nouveau-123"}).json() == {"ok": True}
    site.login(USER, "nouveau-123")
    assert site.client().post("/api/login", json={"username": USER, "password": USER_PASSWORD}).status_code == 401


def test_changer_mot_de_passe_ferme_les_autres_sessions(site):
    site.add_user()
    pc, tel = site.login(USER, USER_PASSWORD), site.login(USER, USER_PASSWORD)
    pc.post("/api/me/password", json={"old": USER_PASSWORD, "new": "nouveau-123"})
    assert pc.get("/api/me").status_code == 200, "la session en cours reste ouverte"
    assert tel.get("/api/me").status_code == 401, "les autres sessions sont fermées"


# ---------- Séparation des deux sites ----------
def test_site_ne_connait_pas_l_admin(client):
    for path in ("/admin.html", "/api/admin/health", "/api/admin/gpu", "/api/admin/dataset.jsonl"):
        assert client.get(path).status_code == 404, f"{path} doit être introuvable sur le port 8093"


def test_tableau_de_bord_refuse_un_compte_normal(admin_site):
    admin_site.add_user()
    c = admin_site.login(USER, USER_PASSWORD)
    assert c.get("/api/admin/health").status_code == 403
    r = c.get("/admin.html")
    assert r.status_code == 303 and "refus=1" in r.headers["location"]


def test_tableau_de_bord_redirige_les_pages_du_site(admin_site):
    admin_site.add_user("chef", "chef-pass-123", is_admin=True)
    c = admin_site.login("chef", "chef-pass-123")
    assert c.get("/api/site").json() == {"mode": "admin"}
    for page in ("/", "/index.html", "/chat.html"):
        r = c.get(page)
        assert r.status_code == 303 and r.headers["location"] == "/admin.html"


def test_ancienne_page_absente_du_tableau_de_bord(admin_site):
    admin_site.add_user("chef", "chef-pass-123", is_admin=True)
    c = admin_site.login("chef", "chef-pass-123")
    assert c.post("/prompt", data={"idea": "x"}).status_code in (404, 405)
