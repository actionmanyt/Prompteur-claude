# SPDX-License-Identifier: AGPL-3.0-or-later
"""Garde-fous sur Modelfile-prompteur-claude.txt : base, réglages, encodage des accents."""
import re

import pytest

from conftest import ROOT

PATH = ROOT / "Modelfile-prompteur-claude.txt"


@pytest.fixture(scope="module")
def modelfile():
    raw = PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "pas de BOM en tête du fichier"
    return raw.decode("utf-8")


def test_base_qwen3_4b(modelfile):
    froms = re.findall(r"(?m)^FROM\s+(\S+)", modelfile)
    assert froms == ["qwen3:4b-instruct"], "un 7-8B déborderait des 6 Go de la GTX 1060"


def test_pas_de_gabarit_ni_licence_d_un_autre_modele(modelfile):
    assert not re.search(r"(?m)^(TEMPLATE|LICENSE)\b", modelfile), "garder le gabarit de qwen3"


def test_reglages_valides(modelfile, core):
    params = dict(re.findall(r"(?m)^PARAMETER\s+(\S+)\s+(\S+)", modelfile))
    assert int(params["num_ctx"]) == core.NUM_CTX, "même num_ctx que le budget de tokens de l'app"
    assert params["num_predict"] == "400"
    assert params["temperature"] == "0.3"


def test_accents_intacts(modelfile):
    casses = re.findall(r"├.|Ã.", modelfile)
    assert not casses, f"accents abîmés (mauvais encodage) : {casses[:5]}"


def test_quatre_sections_dans_les_consignes(modelfile):
    for titre in ("## Contexte", "## Tâche", "## Contraintes", "## Format"):
        assert titre in modelfile
