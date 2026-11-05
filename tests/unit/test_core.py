# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests unitaires de core.py : tokens, mots de passe, chiffrement, outils, démarrage de la base."""
import json
import os
import sqlite3

import pytest


# ---------- Comptage des tokens ----------
@pytest.mark.parametrize("text, expected", [
    ("", 0), (None, 0), ("abc", 2), ("a" * 5, 2), ("a" * 6, 3), ("a" * 400, 160),
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


@pytest.mark.parametrize("pw, ok", [
    ("12345678", True), ("1234567", False), (" avec-espace", False), ("x" * 257, False), ("x" * 256, True),
])
def test_regles_du_mot_de_passe(core, pw, ok):
    assert (core.password_problem(pw) is None) == ok


@pytest.mark.parametrize("name, ok", [
    ("marie", True), ("paul.dupont", True), ("a_b-c", True), ("a", False), ("élodie", False),
    ("avec espace", False), ("x" * 33, False),
])
def test_regles_de_l_identifiant(core, name, ok):
    assert bool(core.USERNAME_RE.match(name)) == ok


def test_migration_base_0_6(core):
    """Une base créée par la 0.6 (sans must_change) est mise à jour au démarrage."""
    with core.db() as con:
        con.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
                    "pw_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL)")
        con.execute("INSERT INTO users(username, pw_hash, is_admin, created) VALUES ('ancien', ?, 1, 'x')",
                    (core.hash_pw("ancien-pass-1"),))
    core.init_db()
    core.init_db()  # deux fois : les deux conteneurs démarrent ensemble
    with core.db() as con:
        r = con.execute("SELECT username, must_change FROM users").fetchone()
    assert (r["username"], r["must_change"]) == ("ancien", 0)


def test_compte_provisoire(core):
    core.init_db()
    core.create_user("marie", "provisoire-1", must_change=True)
    with core.db() as con:
        assert con.execute("SELECT must_change FROM users WHERE username='marie'").fetchone()[0] == 1


def test_nom_utilisateur_unique(core):
    core.init_db()
    with pytest.raises(sqlite3.IntegrityError):
        core.create_user(" admin ", "autre-mot-de-passe")


# ---------- Appel à Ollama : messages d'erreur et délai (0.7.1) ----------
import io
import urllib.error


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _http_error(code, body):
    return urllib.error.HTTPError("http://ollama/api/chat", code, "Bad Request", {}, io.BytesIO(body))


def test_ollama_refus_affiche_la_raison_d_ollama(core, monkeypatch):
    def urlopen(req, timeout):
        raise _http_error(400, b'{"error":"the input length exceeds the context length"}')
    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)
    with pytest.raises(core.OllamaError) as e:
        list(core.ollama_stream("m", []))
    assert "HTTP 400" in str(e.value) and "exceeds the context length" in str(e.value)


def test_ollama_refus_sans_json(core, monkeypatch):
    def urlopen(req, timeout):
        raise _http_error(400, b"texte brut")
    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)
    with pytest.raises(core.OllamaError, match="texte brut"):
        list(core.ollama_stream("m", []))


def test_ollama_delai_reglable(core, monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen["timeout"] = timeout
        raise TimeoutError()
    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(core, "OLLAMA_TIMEOUT", 900)
    with pytest.raises(core.OllamaError, match="900 s"):
        list(core.ollama_stream("m", []))
    assert seen["timeout"] == 900


def test_ollama_delai_par_defaut(core):
    assert core.OLLAMA_TIMEOUT == 300


def test_ollama_flux_normal_et_erreur_en_route(core, monkeypatch):
    lines = b'{"message":{"content":"## Contexte"}}\n\n{"message":{"content":" ok"},"done":true}\n'
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda req, timeout: _FakeResponse(lines))
    assert "".join(core.ollama_stream("m", [])) == "## Contexte ok"
    monkeypatch.setattr(core.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResponse(b'{"error":"model not found"}\n'))
    with pytest.raises(core.OllamaError, match="model not found"):
        list(core.ollama_stream("m", []))


def test_tokens_mesure_reelle_qwen3(core):
    """0.7.1 : un document estimé 5 795 tokens avec l'ancienne règle (4 caractères) en faisait
    9 935 pour qwen3. La nouvelle estimation doit le bloquer avant Ollama."""
    texte = "a" * (5795 * 4)
    assert core.tokens(texte) > core.DOC_BUDGET


def test_demarrage_recompte_les_tokens_des_documents(core):
    core.init_db()
    secs = [{"title": "A", "text": "a" * 100, "on": True}, {"title": "B", "text": "b" * 50, "on": False}]
    with core.db() as con:
        doc_id = con.execute(
            "INSERT INTO documents(name, date, status, markdown, sections, tokens, tokens_full) "
            "VALUES ('x', 'd', 'prêt', ?, ?, 25, 38)", ("m" * 150, json.dumps(secs))).lastrowid
        core.migrate(con)
        r = con.execute("SELECT tokens, tokens_full FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert (r["tokens"], r["tokens_full"]) == (40, 60)
