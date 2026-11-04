# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests d'intégration : bibliothèque (envoi, conversion, sections, étiquettes) et contextes.

Avec le client de test, les tâches de fond (conversion) s'exécutent juste après la réponse :
le document est donc déjà « prêt » (ou en erreur) quand on le relit.
"""
import io
import os

MD = "# Rapport\n\nIntro.\n\n## Partie A\n\nTexte A.\n\n## Partie B\n\nTexte B."


def upload(client, name, data):
    return client.post("/api/library", files=[("files", (name, io.BytesIO(data), "application/octet-stream"))])


def test_envoi_et_conversion_markdown(client):
    r = upload(client, "rapport.md", MD.encode())
    assert r.status_code == 200
    doc_id = r.json()[0]["id"]
    d = client.get(f"/api/library/{doc_id}").json()
    assert d["status"] == "prêt" and d["type"] == "Markdown"
    assert [s["title"] for s in d["sections"]] == ["Rapport", "Partie A", "Partie B"]
    assert all("tokens" in s and s["on"] for s in d["sections"])
    assert d["tokens"] > 0 and d["tokens_full"] > 0


def test_liste_au_format_attendu_par_la_page(client):
    upload(client, "a.txt", b"Bonjour")
    docs = client.get("/api/library").json()
    assert len(docs) == 1
    attendu = {"id", "name", "type", "date", "size", "tokens", "tags", "status"}
    assert attendu <= set(docs[0]), "les champs lus par index.html doivent être présents"
    assert "markdown" not in docs[0] and "sections" not in docs[0], "la liste ne renvoie pas le texte complet"


def test_plusieurs_fichiers_en_un_envoi(client):
    r = client.post("/api/library", files=[
        ("files", ("a.txt", io.BytesIO(b"A"), "text/plain")),
        ("files", ("b.txt", io.BytesIO(b"B"), "text/plain")),
    ])
    assert [x["name"] for x in r.json()] == ["a.txt", "b.txt"]


def test_format_non_pris_en_charge(client):
    r = upload(client, "scan.png", b"\x89PNG")
    d = client.get(f"/api/library/{r.json()[0]['id']}").json()
    assert d["status"] == "non pris en charge"
    assert "OCR" in d["error"]


def test_fichier_vide_refuse(client):
    r = upload(client, "vide.txt", b"   ")
    d = client.get(f"/api/library/{r.json()[0]['id']}").json()
    assert d["status"] == "non pris en charge" and "aucun texte" in d["error"]


def test_fichier_trop_volumineux(site, client, monkeypatch):
    monkeypatch.setattr(site.m.library, "UPLOAD_MAX", 10)
    r = upload(client, "gros.txt", b"x" * 100)
    assert r.json() == [{"name": "gros.txt", "error": "trop volumineux (50 Mo au maximum)"}]
    assert client.get("/api/library").json() == []


def test_nom_de_fichier_nettoye(client):
    r = upload(client, "../../etc/passwd.txt", b"x")
    assert r.json()[0]["name"] == "passwd.txt", "le chemin envoyé ne doit jamais être gardé"


def test_erreur_de_conversion_inattendue(site, client, monkeypatch):
    def boom(path, name):
        raise ValueError("fichier abîmé")
    monkeypatch.setattr(site.m.library, "file_to_md", boom)
    r = upload(client, "a.docx", b"PK")
    d = client.get(f"/api/library/{r.json()[0]['id']}").json()
    assert d["status"] == "erreur" and d["error"] == "fichier abîmé"


def test_fichier_temporaire_supprime(site, client):
    upload(client, "a.txt", b"Bonjour")
    assert os.listdir(site.m.core.TMP_DIR) == []


# ---------- Adresses web ----------
def test_adresse_invalide(client):
    for url in ("ftp://exemple.fr", "exemple.fr", "javascript:alert(1)", ""):
        assert client.post("/api/library/url", json={"url": url}).status_code == 400


def test_adresse_convertie(site, client, monkeypatch):
    monkeypatch.setattr(site.m.library, "url_to_md", lambda url: ("# Page\n\nTexte", "Page web"))
    r = client.post("/api/library/url", json={"url": "https://exemple.fr/article/"})
    assert r.json()["name"] == "exemple.fr/article"
    d = client.get(f"/api/library/{r.json()['id']}").json()
    assert d["status"] == "prêt" and d["source_url"] == "https://exemple.fr/article/"


def test_adresse_bloquee(site, client, monkeypatch):
    def blocked(url):
        raise site.m.convert.NotSupported("site qui bloque les robots")
    monkeypatch.setattr(site.m.library, "url_to_md", blocked)
    r = client.post("/api/library/url", json={"url": "https://www.ebay.fr/itm/1"})
    assert client.get(f"/api/library/{r.json()['id']}").json()["status"] == "non pris en charge"


# ---------- Modification ----------
def test_choix_des_sections_recalcule_les_tokens(client):
    doc_id = upload(client, "r.md", MD.encode()).json()[0]["id"]
    avant = client.get(f"/api/library/{doc_id}").json()
    d = client.patch(f"/api/library/{doc_id}", json={"sections_on": [True, False, False]}).json()
    assert 0 < d["tokens"] < avant["tokens"]
    assert d["tokens_full"] == avant["tokens_full"], "le total du document ne change pas"


def test_choix_des_sections_mauvais_nombre(client):
    doc_id = upload(client, "r.md", MD.encode()).json()[0]["id"]
    assert client.patch(f"/api/library/{doc_id}", json={"sections_on": [True]}).status_code == 400


def test_etiquettes_normalisees(client):
    doc_id = upload(client, "a.txt", b"x").json()[0]["id"]
    d = client.patch(f"/api/library/{doc_id}", json={"tags": [" Devis ", "devis", "", "Achat"]}).json()
    assert d["tags"] == ["achat", "devis"]


def test_renommer_et_nom_vide_ignore(client):
    doc_id = upload(client, "a.txt", b"x").json()[0]["id"]
    assert client.patch(f"/api/library/{doc_id}", json={"name": "Nouveau"}).json()["name"] == "Nouveau"
    assert client.patch(f"/api/library/{doc_id}", json={"name": "   "}).json()["name"] == "Nouveau"


def test_document_introuvable(client):
    assert client.get("/api/library/999").status_code == 404
    assert client.patch("/api/library/999", json={"name": "x"}).status_code == 404


def test_supprimer_document(client):
    doc_id = upload(client, "a.txt", b"x").json()[0]["id"]
    assert client.delete(f"/api/library/{doc_id}").json() == {"ok": True}
    assert client.get(f"/api/library/{doc_id}").status_code == 404


# ---------- Contextes ----------
def test_contexte_par_defaut(client):
    ctx = client.get("/api/contexts").json()
    assert [c["name"] for c in ctx] == ["Mon contexte"]
    assert "GTX 1060" in ctx[0]["text"]


def test_contextes_creer_modifier_supprimer(client):
    c = client.post("/api/contexts", json={"name": " Travail ", "text": " Texte "}).json()
    assert c["name"] == "Travail" and c["text"] == "Texte"
    client.put(f"/api/contexts/{c['id']}", json={"name": "Boulot", "text": "Autre"})
    noms = [x["name"] for x in client.get("/api/contexts").json()]
    assert noms == ["Boulot", "Mon contexte"], "liste triée par nom"
    client.delete(f"/api/contexts/{c['id']}")
    assert len(client.get("/api/contexts").json()) == 1


def test_contexte_sans_nom(client):
    assert client.post("/api/contexts", json={"name": "  ", "text": "x"}).status_code == 400
    assert client.put("/api/contexts/1", json={"name": "", "text": "x"}).status_code == 400
