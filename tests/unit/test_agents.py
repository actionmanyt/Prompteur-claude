# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests unitaires de Mes IA : découpage en extraits, vecteurs, consigne, historique tronqué."""
import pytest


@pytest.fixture
def agents(site):
    return site.m.agents


def test_decoupage_respecte_la_taille_et_les_sections(agents):
    long = "\n\n".join(f"Paragraphe {i} " + "mot " * 60 for i in range(12))
    secs = [{"title": "A", "text": long, "on": True}, {"title": "B", "text": "court", "on": True},
            {"title": "C", "text": "ignoré", "on": False}]
    chunks = agents.chunk_sections(secs)
    assert all(len(t) <= agents.CHUNK_CHARS + agents.CHUNK_OVERLAP + 2 for _, t in chunks)
    assert {t for t, _ in chunks} == {"A", "B"} and chunks[-1] == ("B", "court")
    assert len([c for c in chunks if c[0] == "A"]) >= 3


def test_paragraphe_geant_coupe(agents):
    chunks = agents.chunk_sections([{"title": "X", "text": "a" * 5000, "on": True}])
    assert len(chunks) >= 4


def test_vecteurs_normalises(agents):
    v = agents.unpack(agents.pack([3.0, 4.0]))
    assert abs(v[0] - 0.6) < 1e-6 and abs(v[1] - 0.8) < 1e-6


def test_mots_sans_accents_ni_mots_vides(agents):
    assert agents.words("Les disques de l'Été sont prêts") == ["disques", "ete", "prets"]


def test_consigne_en_quatre_sections(agents):
    s = agents.system_prompt({"context": "C", "task": "T", "constraints": "", "format": "F"}, True)
    assert s.startswith("## Contexte\nC\n\n## Tâche\nT\n\n## Format\nF") and "[1]" in s
    assert agents.system_prompt({}, False) == ""


def test_cibles(agents):
    assert agents.parse_target("ollama:qwen3:4b") == ("ollama", "qwen3:4b")
    with pytest.raises(ValueError):
        agents.parse_target("agent:")


def test_historique_tronque(agents):
    msgs = [{"role": "user", "content": "a" * 400}, {"role": "assistant", "content": "b" * 400},
            {"role": "user", "content": "c" * 40}]
    assert agents.fit_history(msgs, 50) == [msgs[-1]]
    assert agents.fit_history(msgs, 10_000) == msgs


def test_slug(agents):
    assert agents.slug("Expert Réseau & Wi-Fi !") == "expert-reseau-wi-fi"
    assert agents.slug("!!!") == "ia"
