# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests d'intégration : prompts idéaux écrits ou corrigés à la main dans le tableau de bord (0.8.0)."""
import json
import sqlite3

import pytest

from conftest import add_ready_doc

PROMPT = "## Contexte\nTexte.\n\n## Tâche\nRédige.\n\n## Contraintes\n- Aucune.\n\n## Format\nUn paragraphe."


@pytest.fixture
def admin(admin_site):
    admin_site.m.core.create_user("chef", "chef-pass-123", is_admin=True)
    return admin_site.login("chef", "chef-pass-123")


def add_generated(site, idea="Idée générée", prompt="Prompt raté", doc=None):
    core = site.m.core
    with core.db() as con:
        hid = con.execute("INSERT INTO history(date, idea, context_id, model, prompt) VALUES (?,?,1,?,?)",
                          (core.now_iso(), idea, "prompteur-claude", prompt)).lastrowid
        if doc:
            con.execute("INSERT INTO history_documents VALUES (?,?,?)", (hid, doc, "texte.md"))
    return hid


def entry(client, hid):
    return next(h for h in client.get("/api/history").json() if h["id"] == hid)


# ---------- Ajout ----------
def test_ajouter_un_prompt_ideal(admin):
    r = admin.post("/api/admin/ideal", json={"idea": " Gains potentiels ", "excerpt": "Mon texte.", "prompt": PROMPT})
    assert r.status_code == 200
    h = entry(admin, r.json()["id"])
    assert h["ideal"] and h["manual"] and h["model"] == "manuel"
    assert (h["idea"], h["excerpt"], h["prompt"]) == ("Gains potentiels", "Mon texte.", PROMPT)
    assert h["source"] == [] and h["duration_s"] == 0 and h["tokens_after"] > 0


@pytest.mark.parametrize("body, msg", [
    ({"idea": "  ", "prompt": PROMPT}, "idée"),
    ({"idea": "x", "prompt": " "}, "prompt idéal"),
    ({"idea": "x" * 2001, "prompt": PROMPT}, "Idée trop longue"),
    ({"idea": "x", "prompt": "p" * 20001}, "Prompt trop long"),
    ({"idea": "x", "prompt": PROMPT, "excerpt": "mot " * 20000}, "Extrait trop long"),
])
def test_ajout_refuse(admin, body, msg):
    r = admin.post("/api/admin/ideal", json=body)
    assert r.status_code == 400 and msg in r.json()["error"]


def test_corriger_un_prompt_genere(admin_site, admin):
    doc = add_ready_doc(admin_site, "texte.md", "Le texte écrit avant.")
    src = add_generated(admin_site, doc=doc)
    r = admin.post("/api/admin/ideal", json={"idea": "Idée générée", "prompt": PROMPT, "from_id": src})
    new = entry(admin, r.json()["id"])
    assert new["source"] == ["texte.md"] and new["doc_ids"] == [doc] and new["context_id"] == 1
    old = entry(admin, src)  # l'original n'est pas touché
    assert old["prompt"] == "Prompt raté" and not old["ideal"] and not old["manual"]


def test_corriger_une_entree_absente(admin):
    r = admin.post("/api/admin/ideal", json={"idea": "x", "prompt": PROMPT, "from_id": 999})
    assert r.status_code == 404


# ---------- Modification ----------
def test_modifier_un_prompt_manuel(admin):
    hid = admin.post("/api/admin/ideal", json={"idea": "x", "prompt": "v1", "excerpt": "e"}).json()["id"]
    assert admin.put(f"/api/admin/ideal/{hid}", json={"idea": "y", "prompt": PROMPT}).status_code == 200
    h = entry(admin, hid)
    assert (h["idea"], h["prompt"], h["excerpt"]) == ("y", PROMPT, "")


def test_un_prompt_genere_ne_se_modifie_pas(admin_site, admin):
    hid = add_generated(admin_site)
    r = admin.put(f"/api/admin/ideal/{hid}", json={"idea": "y", "prompt": PROMPT})
    assert r.status_code == 400 and entry(admin, hid)["prompt"] == "Prompt raté"
    assert admin.put("/api/admin/ideal/999", json={"idea": "y", "prompt": PROMPT}).status_code == 404


# ---------- Accès ----------
def test_route_absente_du_site(site):
    r = site.login().post("/api/admin/ideal", json={"idea": "x", "prompt": PROMPT})
    assert r.status_code == 404


def test_reserve_aux_admins(admin_site):
    admin_site.add_user()
    r = admin_site.login("alice", "alice-pass-123").post("/api/admin/ideal", json={"idea": "x", "prompt": PROMPT})
    assert r.status_code == 403


# ---------- Export ----------
def test_export_avec_extrait_saisi(admin_site, admin):
    doc = add_ready_doc(admin_site, "texte.md", "Texte complet du document.")
    src = add_generated(admin_site, doc=doc)
    admin.post("/api/admin/ideal", json={"idea": "Avec extrait", "excerpt": "Résumé saisi.", "prompt": PROMPT,
                                         "from_id": src})
    admin.post("/api/admin/ideal", json={"idea": "Sans extrait", "prompt": PROMPT, "from_id": src})
    lines = {x["idea"]: x for x in map(json.loads, admin.get("/api/admin/dataset.jsonl").text.splitlines())}
    assert lines["Avec extrait"]["excerpt"] == "Résumé saisi."
    assert lines["Sans extrait"]["excerpt"] == "Texte complet du document."
    assert lines["Avec extrait"]["sources"] == ["texte.md"]


# ---------- Migration d'une base 0.7 ----------
def test_migration_ajoute_les_colonnes(admin_site):
    ideals, core = admin_site.m.ideals, admin_site.m.core
    con = sqlite3.connect(core.DB_PATH)
    con.executescript("ALTER TABLE history DROP COLUMN excerpt; ALTER TABLE history DROP COLUMN manual;")
    con.close()
    ideals.migrate_db()
    ideals.migrate_db()  # refaisable
    with core.db() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(history)")}
    assert {"excerpt", "manual"} <= cols
