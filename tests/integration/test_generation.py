# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests d'intégration : génération du prompt (Ollama imité), budget de tokens, historique."""
from conftest import add_ready_doc, fake_stream, ndjson_lines

REPONSE = ["```markdown\n", "## Contexte\nDevis de 100 €.\n", "## Tâche\nComparer.\n", "```"]


def generate(client, **body):
    body.setdefault("idea", "Compare les prix")
    return client.post("/api/generate", json=body)


def test_idee_obligatoire(client):
    r = generate(client, idea="   ")
    assert r.status_code == 400 and "idée" in r.json()["error"]


def test_document_absent_ou_pas_pret(site, client):
    assert generate(client, doc_ids=[999]).status_code == 400
    with site.m.core.db() as con:
        doc_id = con.execute("INSERT INTO documents(name, date, status) VALUES ('a', 'x', 'en cours')").lastrowid
    assert "pas encore prêt" in generate(client, doc_ids=[doc_id]).json()["error"]


def test_budget_de_tokens_verifie_par_le_serveur(site, client, monkeypatch):
    doc_id = add_ready_doc(site, text="x" * 4000)  # environ 1000 tokens
    monkeypatch.setattr(site.m.prompts, "DOC_BUDGET", 500)
    r = generate(client, doc_ids=[doc_id])
    assert r.status_code == 400 and "trop pour num_ctx" in r.json()["error"]


def test_budget_reel_6992(site, client, monkeypatch):
    """Un document juste sous le budget passe, juste au-dessus est refusé."""
    monkeypatch.setattr(site.m.prompts, "ollama_stream", fake_stream(["ok"]))
    ok = add_ready_doc(site, "ok.txt", "a" * int(6992 * 2.5))
    trop = add_ready_doc(site, "trop.txt", "a" * (int(6992 * 2.5) + 1))
    assert generate(client, doc_ids=[ok]).status_code == 200
    assert generate(client, doc_ids=[trop]).status_code == 400


def test_generation_en_streaming(site, client, monkeypatch):
    fake = fake_stream(REPONSE)
    monkeypatch.setattr(site.m.prompts, "ollama_stream", fake)
    doc_id = add_ready_doc(site, "devis.md", "Prix : 100 €")
    r = generate(client, doc_ids=[doc_id], context_id=1, attach=True)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = ndjson_lines(r)
    assert [x["t"] for x in lines[:-1]] == REPONSE, "chaque morceau d'Ollama est renvoyé tel quel"
    done = lines[-1]
    assert done["done"] is True and isinstance(done["id"], int)
    assert done["prompt"].startswith("## Contexte"), "le bloc ``` d'Ollama est retiré"
    assert "## Document 1 : devis.md" in done["prompt"], "sections gardées jointes après le prompt"
    # Ce qui a été envoyé au modèle
    model, messages = fake.calls[0][0], fake.calls[0][1]
    assert model == "prompteur-claude"
    sent = messages[0]["content"]
    assert "Compare les prix" in sent and "Prix : 100 €" in sent and "GTX 1060" in sent


def test_generation_sans_jointure(site, client, monkeypatch):
    monkeypatch.setattr(site.m.prompts, "ollama_stream", fake_stream(["## Contexte\nA"]))
    doc_id = add_ready_doc(site)
    done = ndjson_lines(generate(client, doc_ids=[doc_id], attach=False))[-1]
    assert "## Document" not in done["prompt"]


def test_deux_documents_ajoutent_les_contraintes(site, client, monkeypatch):
    fake = fake_stream(["ok"])
    monkeypatch.setattr(site.m.prompts, "ollama_stream", fake)
    a, b = add_ready_doc(site, "a.md", "A"), add_ready_doc(site, "b.md", "B")
    generate(client, doc_ids=[a, b, a])  # doublon ignoré
    sent = fake.calls[0][1][0]["content"]
    assert "contradictions" in sent
    assert sent.count("### Document") == 2


def test_historique_enregistre(site, client, monkeypatch):
    monkeypatch.setattr(site.m.prompts, "ollama_stream", fake_stream(REPONSE))
    a, b = add_ready_doc(site, "a.md", "A"), add_ready_doc(site, "b.md", "B")
    done = ndjson_lines(generate(client, doc_ids=[a, b]))[-1]
    h = client.get("/api/history").json()
    assert len(h) == 1
    e = h[0]
    assert e["id"] == done["id"] and e["source"] == ["a.md", "b.md"] and e["doc_ids"] == [a, b]
    attendu = {"id", "date", "idea", "source", "tokens_before", "tokens_after", "duration_s", "model", "ideal", "prompt"}
    assert attendu <= set(e), "champs lus par admin.html"
    assert e["model"] == "prompteur-claude" and e["ideal"] is False
    assert e["tokens_after"] == done["tokens_after"] > 0


def test_ollama_injoignable(site, client, monkeypatch):
    monkeypatch.setattr(site.m.prompts, "ollama_stream", fake_stream(["début"], error=ConnectionError("refus")))
    lines = ndjson_lines(generate(client))
    assert "Prompteur injoignable" in lines[-1]["error"]
    assert client.get("/api/history").json() == [], "rien n'est enregistré si la génération échoue"


def test_prompt_ideal_bascule(site, client, monkeypatch):
    monkeypatch.setattr(site.m.prompts, "ollama_stream", fake_stream(["ok"]))
    hid = ndjson_lines(generate(client))[-1]["id"]
    assert client.post(f"/api/history/{hid}/ideal").json() == {"ok": True, "ideal": True}
    assert client.post(f"/api/history/{hid}/ideal").json() == {"ok": True, "ideal": False}
    assert client.post("/api/history/999/ideal").status_code == 404


def test_supprimer_entree_historique(site, client, monkeypatch):
    monkeypatch.setattr(site.m.prompts, "ollama_stream", fake_stream(["ok"]))
    hid = ndjson_lines(generate(client))[-1]["id"]
    client.delete(f"/api/history/{hid}")
    assert client.get("/api/history").json() == []


def test_refus_d_ollama_affiche_sa_raison(site, client, monkeypatch):
    """0.7.1 : la page affiche la raison donnée par Ollama, pas « Prompteur injoignable »."""
    err = site.m.core.OllamaError("demande refusée (HTTP 400) : the input length exceeds the context length")
    monkeypatch.setattr(site.m.prompts, "ollama_stream", fake_stream([], error=err))
    lines = ndjson_lines(generate(client))
    assert lines[-1]["error"].startswith("Ollama : demande refusée (HTTP 400)")
    assert "context length" in lines[-1]["error"]


def test_document_trop_long_pour_ollama_message_clair(site, client, monkeypatch):
    """0.7.1 : si l'estimation passe mais qu'Ollama refuse, la page dit combien retirer."""
    err = site.m.core.OllamaError("demande refusée (HTTP 400) : request (9935 tokens) exceeds "
                                  "the available context size (8192 tokens), try increasing it")
    monkeypatch.setattr(site.m.prompts, "ollama_stream", fake_stream([], error=err))
    msg = ndjson_lines(generate(client))[-1]["error"]
    assert msg.startswith("Trop long pour le modèle : 9935 tokens réels pour 8192 disponibles")
    assert "Retirez environ 23 % des sections" in msg
