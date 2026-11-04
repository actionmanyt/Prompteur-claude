# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests d'intégration : comptes créés depuis le tableau de bord, mot de passe provisoire."""
import pytest

from conftest import USER, USER_PASSWORD

CHEF, CHEF_PW = "chef", "chef-pass-123"


@pytest.fixture
def admin(admin_site):
    admin_site.add_user(CHEF, CHEF_PW, is_admin=True)
    return admin_site.login(CHEF, CHEF_PW)


def users(admin):
    return {u["username"]: u for u in admin.get("/api/admin/users").json()}


def create(admin, username=USER, password=USER_PASSWORD, **extra):
    return admin.post("/api/admin/users", json={"username": username, "password": password, **extra})


# ---------- Accès ----------
def test_gestion_des_comptes_absente_du_site(client):
    assert client.get("/api/admin/users").status_code == 404
    assert client.post("/api/admin/users", json={"username": "x", "password": "y" * 8}).status_code == 404


def test_gestion_des_comptes_refusee_a_un_utilisateur(admin_site):
    admin_site.add_user()
    c = admin_site.login(USER, USER_PASSWORD)
    assert c.get("/api/admin/users").status_code == 403
    assert create(c, "pirate").status_code == 403


# ---------- Création ----------
def test_creer_un_compte_provisoire(admin_site, admin):
    r = create(admin)
    assert r.status_code == 200 and r.json()["username"] == USER
    u = users(admin)[USER]
    assert u["is_admin"] is False and u["must_change"] is True and u["sessions"] == 0
    assert "pw_hash" not in u, "le hachage du mot de passe ne sort jamais"


def test_creer_un_compte_admin_definitif(admin):
    create(admin, "paul", "paul-pass-123", is_admin=True, temporary=False)
    u = users(admin)["paul"]
    assert u["is_admin"] is True and u["must_change"] is False


@pytest.mark.parametrize("username", ["a", "avec espace", "élodie", "x" * 33, "", "../admin"])
def test_identifiant_refuse(admin, username):
    assert create(admin, username).status_code == 400


def test_mot_de_passe_trop_court_refuse(admin):
    r = create(admin, password="court")
    assert r.status_code == 400 and "8 caractères" in r.json()["error"]


def test_compte_en_double(admin):
    create(admin)
    assert create(admin).status_code == 409
    assert create(admin, USER.upper()).status_code == 409, "Alice et alice : même compte"


def test_compte_cree_sur_8094_utilisable_sur_8093(admin_site, admin, site):
    """Les deux conteneurs partagent la même base (ici, le même dossier de test)."""
    create(admin, temporary=False)
    c = site.login(USER, USER_PASSWORD)
    assert c.get("/api/me").json()["username"] == USER
    assert c.get("/api/library").status_code == 200


# ---------- Mot de passe provisoire ----------
def test_provisoire_bloque_tout_sauf_le_changement(admin_site, admin):
    create(admin, "paul", "provisoire-1", is_admin=True)
    c = admin_site.login("paul", "provisoire-1")
    assert c.get("/api/me").json()["must_change"] is True
    r = c.get("/api/admin/users")
    assert r.status_code == 403 and r.json()["must_change"] is True
    assert c.post("/api/me/password", json={"old": "provisoire-1", "new": "definitif-123"}).json() == {"ok": True}
    assert c.get("/api/me").json()["must_change"] is False
    assert c.get("/api/admin/users").status_code == 200


def test_provisoire_sur_le_site(site):
    site.m.core.create_user(USER, "provisoire-1", must_change=True)
    c = site.login(USER, "provisoire-1")
    for path in ("/api/library", "/api/history", "/api/contexts", "/api/chat/settings"):
        assert c.get(path).status_code == 403, path
    assert c.post("/api/generate", json={"idea": "x"}).status_code == 403
    assert c.get("/index.html").status_code == 200, "la page s'ouvre pour afficher la fenêtre de changement"
    assert c.post("/api/logout").status_code == 200


# ---------- Nouveau mot de passe ----------
def test_nouveau_mot_de_passe_ferme_les_sessions(admin_site, admin):
    create(admin, "paul", "paul-pass-123", is_admin=True, temporary=False)
    paul = admin_site.login("paul", "paul-pass-123")
    uid = users(admin)["paul"]["id"]
    assert users(admin)["paul"]["sessions"] == 1
    assert admin.post(f"/api/admin/users/{uid}/password", json={"password": "nouveau-123"}).json()["ok"] is True
    assert paul.get("/api/me").status_code == 401
    assert users(admin)["paul"]["must_change"] is True
    admin_site.login("paul", "nouveau-123")


def test_nouveau_mot_de_passe_pour_soi_jamais_provisoire(admin_site, admin):
    uid = users(admin)[CHEF]["id"]
    r = admin.post(f"/api/admin/users/{uid}/password", json={"password": "chef-nouveau-1", "temporary": True}).json()
    assert r["self"] is True
    c = admin_site.login(CHEF, "chef-nouveau-1")
    assert c.get("/api/me").json()["must_change"] is False


def test_nouveau_mot_de_passe_erreurs(admin):
    assert admin.post("/api/admin/users/999/password", json={"password": "nouveau-123"}).status_code == 404
    uid = users(admin)[CHEF]["id"]
    assert admin.post(f"/api/admin/users/{uid}/password", json={"password": "court"}).status_code == 400


# ---------- Rôle, sessions, suppression ----------
def test_rendre_admin_puis_retirer(admin):
    create(admin)
    uid = users(admin)[USER]["id"]
    assert admin.patch(f"/api/admin/users/{uid}", json={"is_admin": True}).json()["is_admin"] is True
    assert users(admin)[USER]["is_admin"] is True
    admin.patch(f"/api/admin/users/{uid}", json={"is_admin": False})
    assert users(admin)[USER]["is_admin"] is False


def test_impossible_de_retirer_ses_propres_droits(admin):
    uid = users(admin)[CHEF]["id"]
    r = admin.patch(f"/api/admin/users/{uid}", json={"is_admin": False})
    assert r.status_code == 400 and "propres droits" in r.json()["error"]


def test_dernier_admin_protege(admin_site, admin):
    create(admin, "paul", "paul-pass-123", is_admin=True, temporary=False)
    paul = admin_site.login("paul", "paul-pass-123")
    chef_id = users(admin)[CHEF]["id"]
    # paul retire les droits de chef : il reste paul, donc c'est permis
    assert paul.patch(f"/api/admin/users/{chef_id}", json={"is_admin": False}).status_code == 200
    # chef n'est plus admin : il n'a plus accès
    assert admin.get("/api/admin/users").status_code == 403


def test_deconnecter_un_compte(admin_site, admin):
    create(admin, temporary=False)
    admin_site.login(USER, USER_PASSWORD)
    uid = users(admin)[USER]["id"]
    assert admin.post(f"/api/admin/users/{uid}/logout").json() == {"ok": True, "closed": 1}
    assert users(admin)[USER]["sessions"] == 0


def test_supprimer_un_compte(admin_site, admin):
    create(admin, temporary=False)
    c = admin_site.login(USER, USER_PASSWORD)
    uid = users(admin)[USER]["id"]
    with admin_site.m.core.db() as con:
        con.execute("INSERT INTO history(date, user_id, idea, prompt) VALUES ('2026-11-04', ?, 'idée', 'p')", (uid,))
        con.execute("INSERT INTO user_settings(user_id, api_key_enc) VALUES (?, 'chiffré')", (uid,))
    assert admin.delete(f"/api/admin/users/{uid}").json() == {"ok": True}
    assert USER not in users(admin)
    assert c.get("/api/me").status_code == 401, "ses sessions sont fermées"
    with admin_site.m.core.db() as con:
        assert con.execute("SELECT COUNT(*) FROM user_settings").fetchone()[0] == 0, "clé API effacée"
        assert con.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 1, "historique gardé"


def test_impossible_de_se_supprimer(admin):
    uid = users(admin)[CHEF]["id"]
    assert admin.delete(f"/api/admin/users/{uid}").status_code == 400


def test_supprimer_compte_introuvable(admin):
    assert admin.delete("/api/admin/users/999").status_code == 404
    assert admin.patch("/api/admin/users/999", json={"is_admin": True}).status_code == 404
    assert admin.post("/api/admin/users/999/logout").status_code == 404


def test_liste_triee_et_dernier_prompt(admin_site, admin):
    create(admin, "Zoe", "zoe-pass-123")
    create(admin, "bob", "bob-pass-123")
    uid = users(admin)["bob"]["id"]
    with admin_site.m.core.db() as con:
        con.execute("INSERT INTO history(date, user_id, idea, prompt) VALUES ('2026-11-04T10:00:00+00:00', ?, 'i', 'p')", (uid,))
    liste = admin.get("/api/admin/users").json()
    assert [u["username"] for u in liste] == ["bob", CHEF, "Zoe"], "ordre alphabétique sans tenir compte des majuscules"
    assert liste[0]["last_prompt"] == "2026-11-04T10:00:00+00:00"
