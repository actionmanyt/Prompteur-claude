# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests unitaires des fonctions internes de prompts.py, library.py, chat.py et admin.py."""
import io
import json
import urllib.error
from contextlib import contextmanager

import pytest

DOC1 = {"name": "devis.pdf", "text": "Prix : 100 €"}
DOC2 = {"name": "contrat.docx", "text": "Prix : 120 €"}


# ---------- prompts.py ----------
def test_message_idee_seule(site):
    assert site.m.prompts.build_message("  Résume  ", "", [], True) == "Résume"


def test_message_avec_contexte(site):
    msg = site.m.prompts.build_message("Idée", "Serveur Ubuntu", [], True)
    assert "Mon contexte" in msg and "Serveur Ubuntu" in msg


def test_message_un_document_sans_contrainte_multi(site):
    msg = site.m.prompts.build_message("Idée", "", [DOC1], attach=False)
    assert "### Document 1 : devis.pdf" in msg
    assert "Résume fidèlement" in msg
    assert "contradictions" not in msg
    assert "Info technique" not in msg


def test_message_deux_documents_contraintes_ajoutees(site):
    msg = site.m.prompts.build_message("Idée", "", [DOC1, DOC2], attach=True)
    assert "### Document 2 : contrat.docx" in msg
    assert "citer le document concerné" in msg and "contradictions" in msg
    assert "documents ci-dessous" in msg


def test_annexe_titres_decales(site):
    out = site.m.prompts.appendix([{"name": "a.md", "text": "# Titre\n## Sous-titre\ntexte #pas un titre"}])
    assert out == "## Document 1 : a.md\n\n## Titre\n### Sous-titre\ntexte #pas un titre"


# ---------- library.py ----------
def test_texte_garde_selon_sections(site):
    secs = [{"text": "A", "on": True}, {"text": "B", "on": False}, {"text": "C", "on": True}]
    assert site.m.library.kept_text(secs) == "A\n\nC"


# ---------- admin.py ----------
@pytest.mark.parametrize("value, expected", [("42", 42.0), ("12.5", 12.5), ("[N/A]", 0.0), ("", 0.0)])
def test_admin_num(admin_site, value, expected):
    assert admin_site.m.admin.num(value) == expected


@pytest.mark.parametrize("sec, expected", [
    (59, "0 min"), (125, "2 min"), (3 * 3600 + 120, "3 h 2 min"), (2 * 86400 + 5 * 3600, "2 j 5 h"),
])
def test_admin_duree(admin_site, sec, expected):
    assert admin_site.m.admin.duration(sec) == expected


def test_admin_noms_courts(admin_site):
    a = admin_site.m.admin
    assert a.short_cpu("Intel(R) Xeon(R) CPU E5-2670 v3 @ 2.30GHz") == "Intel Xeon E5-2670 v3"
    assert a.short_gpu("NVIDIA GeForce GTX 1060 6GB") == "GTX 1060 6GB"


# ---------- chat.py ----------
def _http_error(code, body=b""):
    return urllib.error.HTTPError("https://api.anthropic.com", code, "x", {}, io.BytesIO(body))


def test_chat_erreurs_api(site):
    c = site.m.chat
    assert c.api_error(_http_error(401)) == "clé API refusée"
    body = json.dumps({"error": {"message": "surchargé"}}).encode()
    assert c.api_error(_http_error(529, body)) == "HTTP 529 surchargé"
    assert c.api_error(_http_error(500, b"pas du json")) == "HTTP 500"
    assert c.api_error(TimeoutError()) == "TimeoutError"


def _fake_urlopen(lines):
    @contextmanager
    def urlopen(req, timeout=None):
        urlopen.request = req
        yield iter(line.encode() + b"\n" for line in lines)
    return urlopen


def _sse(obj):
    return "data: " + json.dumps(obj)


def test_chat_lecture_du_flux_claude(site, monkeypatch):
    c = site.m.chat
    fake = _fake_urlopen([
        "event: message_start",
        _sse({"type": "message_start", "message": {"usage": {"input_tokens": 12}}}),
        _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Bon"}}),
        _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "jour"}}),
        _sse({"type": "message_delta", "usage": {"output_tokens": 3}}),
    ])
    monkeypatch.setattr(c.urllib.request, "urlopen", fake)
    state = {}
    text = "".join(site.original["claude_stream"]("sk-test", "claude-haiku-5-5", [{"role": "user", "content": "x"}], state))
    assert text == "Bonjour"
    assert state == {"tin": 12, "tout": 3}
    assert fake.request.get_header("X-api-key") == "sk-test"


def test_chat_flux_claude_erreur(site, monkeypatch):
    c = site.m.chat
    monkeypatch.setattr(c.urllib.request, "urlopen", _fake_urlopen([
        _sse({"type": "error", "error": {"message": "overloaded"}})]))
    with pytest.raises(RuntimeError, match="overloaded"):
        list(site.original["claude_stream"]("k", "m", [], {}))


def test_chat_reglages_par_defaut(site):
    with site.m.core.db() as con:
        s = site.m.chat.settings_of(con, 999)
    assert s["cap"] == 5.0 and s["default_model"] == "haiku" and s["api_key_enc"] is None
