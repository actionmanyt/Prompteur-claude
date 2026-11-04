# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests unitaires de core.py : tokens, mots de passe, chiffrement, outils, démarrage de la base."""
import json
import os
import sqlite3

import pytest


# ---------- Comptage des tokens ----------
@pytest.mark.parametrize("text, expected", [
    ("", 0), (None, 0), ("abc", 1), ("a" * 4, 1), ("a" * 400, 100),
])
def test_tokens_estimation(core, text, expected):
    assert core.tokens(text) == expected


def test_budget_documents_par_defaut(core):
    assert core.NUM_CTX == 8192
    assert core.DOC_BUDGET == 6992, "budget validé : num_ctx 8192 moins 1200 réservés"


# ---------- Mots de passe ----------
def test_mot_de_passe_correct_et_incorrect(core):
    h = core.hash_pw("secret-123")
    assert h.startswith("scrypt$")
    assert core.check_pw("secret-123", h)
    assert not core.check_pw("secret-124", h)


def test_mot_de_passe_sel_different(core):
    assert core.hash_pw("x") != core.hash_pw("x"), "chaque hachage doit avoir son propre sel"


@pytest.mark.parametrize("stored", ["", "n'importe quoi", "scrypt$zz$zz", "a$b"])
def test_mot_de_passe_hachage_abime(core, stored):
    assert core.check_pw("x", stored) is False


# ---------- Chiffrement de la clé API ----------
def test_chiffrement_aller_retour(core):
    token = core.encrypt("sk-ant-exemple-1234")
    assert "sk-ant" not in token
    assert core.decrypt(token) == "sk-ant-exemple-1234"


def test_chiffrement_autre_secret_illisible(core, monkeypatch):
    token = core.encrypt("sk-ant-exemple")
    monkeypatch.setenv("SECRET_KEY", "une-autre-cle")
    with pytest.raises(Exception):
        core.decrypt(token)


def test_chiffrement_sans_secret_cree_secret_key(core, monkeypatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY")
    core.decrypt(core.encrypt("x"))
    key = tmp_path / "secret.key"
    assert key.exists()
    if os.name != "nt":  # Windows ne gère pas les droits Unix ; le serveur, si
        assert oct(key.stat().st_mode & 0o777) == "0o600"


# ---------- Outils ----------
@pytest.mark.parametrize("text, expected", [
    ("```markdown\n## Contexte\nA\n```", "## Contexte\nA"),
    ("```\nA\n```", "A"),
    ("  sans bloc  ", "sans bloc"),
])
def test_strip_fence(core, text, expected):
    assert core.strip_fence(text) == expected


def test_ndjson_garde_les_accents(core):
    line = core.ndjson({"t": "évité"})
    assert line.endswith("\n")
    assert "évité" in line
    assert json.loads(line) == {"t": "évité"}


# ---------- Démarrage de la base ----------
def _count(core, sql):
    with core.db() as con:
        return con.execute(sql).fetchone()[0]


def test_init_db_cree_admin_et_contexte(core):
    core.init_db()
    with core.db() as con:
        u = con.execute("SELECT username, is_admin FROM users").fetchall()
        c = con.execute("SELECT name FROM contexts").fetchall()
    assert [(r["username"], r["is_admin"]) for r in u] == [("admin", 1)]
    assert [r["name"] for r in c] == ["Mon contexte"]


def test_init_db_deux_fois_sans_doublon(core):
    core.init_db()
    core.init_db()
    assert _count(core, "SELECT COUNT(*) FROM users") == 1
    assert _count(core, "SELECT COUNT(*) FROM contexts") == 1


def test_init_db_reprend_context_md(core, tmp_path):
    (tmp_path / "context.md").write_text("Mon ancien contexte", encoding="utf-8")
    core.init_db()
    with core.db() as con:
        assert con.execute("SELECT text FROM contexts").fetchone()[0] == "Mon ancien contexte"


def test_init_db_sans_compte_dans_env(core, monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    core.init_db()
    assert _count(core, "SELECT COUNT(*) FROM users") == 0
    assert "Aucun compte" in capsys.readouterr().out


def test_init_db_conversion_interrompue_passe_en_erreur(core):
    core.init_db()
    with core.db() as con:
        con.execute("INSERT INTO documents(name, date, status) VALUES ('a.pdf', ?, 'en cours')", (core.now_iso(),))
    core.init_db()  # redémarrage du conteneur
    with core.db() as con:
        r = con.execute("SELECT status, error FROM documents").fetchone()
    assert r["status"] == "erreur"
    assert "renvoyez" in r["error"]


def test_init_db_supprime_les_sessions_expirees(core):
    core.init_db()
    with core.db() as con:
        con.execute("INSERT INTO sessions(token, user_id, expires) VALUES ('vieux', 1, 0)")
        con.execute("INSERT INTO sessions(token, user_id, expires) VALUES ('neuf', 1, 9e12)")
    core.init_db()
    with core.db() as con:
        assert [r[0] for r in con.execute("SELECT token FROM sessions")] == ["neuf"]


def test_nom_utilisateur_unique(core):
    core.init_db()
    with pytest.raises(sqlite3.IntegrityError):
        core.create_user(" admin ", "autre-mot-de-passe")
