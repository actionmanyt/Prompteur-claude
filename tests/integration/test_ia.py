# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests d'intégration : Mes IA (rôle en 4 sections + documents), recherche d'extraits, nouveau tchat."""
import json

from conftest import add_ready_doc, fake_stream, ndjson_lines

DOC_SSD = """# Disques du serveur

## SSD système

Le SSD système Kingston est à 1 % de vie. Il faut le remplacer en décembre.

## Disques de données

Deux WD Blue de 2 To servent aux nœuds Storj. Ils sont en bon état.
"""


def agent_body(**extra):
    return {"name": "Expert disques", "base_model": "qwen3:4b-instruct", "context": "Serveur maison",
            "task": "Répondre aux questions sur les disques", "constraints": "Phrases simples",
            "format": "Liste à puces", "temperature": 0.2, **extra}


def fake_vectors(texts):
    """Vecteurs imités : une dimension par mot-clé, assez pour classer les extraits."""
    keys = ["ssd", "storj", "blue", "kingston", "vie"]
    return [[float(t.lower().count(k)) + 0.01 for k in keys] for t in texts]


# ---------- Modèles ----------
def test_liste_des_modeles_sans_les_modeles_de_vecteurs(site, client, monkeypatch):
    tags = {"models": [{"name": "qwen3:4b-instruct", "size": 2500, "details": {"family": "qwen3"}},
                       {"name": "nomic-embed-text:latest", "details": {"family": "nomic-bert"}},
                       {"name": "bge-m3:latest", "details": {"family": "bert"}},
                       {"name": "llama3.2:3b", "details": {"family": "llama"}}]}
    monkeypatch.setattr(site.m.agents, "ollama_json", lambda *a, **k: tags)
    r = client.get("/api/ia/models").json()
    assert [m["name"] for m in r["ollama"]] == ["llama3.2:3b", "qwen3:4b-instruct"]
    assert r["embed_ready"] is True and r["claude"] == ["haiku", "sonnet"] and r["has_key"] is False


def test_modeles_ollama_absent(site, client, monkeypatch):
    monkeypatch.setattr(site.m.agents, "ollama_json", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("x")))
    r = client.get("/api/ia/models").json()
    assert r["ollama"] == [] and r["ollama_error"]


# ---------- Mes IA ----------
def test_creer_modifier_supprimer_une_ia(site, client, monkeypatch):
    monkeypatch.setattr(site.m.agents, "embed", fake_vectors)
    doc = add_ready_doc(site, "disques.md", DOC_SSD)
    a = client.post("/api/ia/agents", json=agent_body(doc_ids=[doc])).json()
    assert a["name"] == "Expert disques" and a["doc_ids"] == [doc]
    assert "## Contexte\nServeur maison" in a["system"] and "## Format\nListe à puces" in a["system"]
    # L'indexation (tâche de fond) a découpé le document et calculé les vecteurs
    a = client.get(f"/api/ia/agents/{a['id']}").json()
    assert a["index"]["chunks"] >= 2 and a["index"]["vectors"] == a["index"]["chunks"]
    a = client.put(f"/api/ia/agents/{a['id']}", json=agent_body(name="Autre", doc_ids=[])).json()
    assert a["name"] == "Autre" and a["doc_ids"] == [] and "Des extraits" not in a["system"]
    assert client.delete(f"/api/ia/agents/{a['id']}").json()["ok"] is True
    assert client.get("/api/ia/agents").json() == []


def test_validation_d_une_ia(client):
    assert client.post("/api/ia/agents", json=agent_body(name=" ")).status_code == 400
    assert client.post("/api/ia/agents", json=agent_body(base_model="")).status_code == 400
    assert client.post("/api/ia/agents", json=agent_body(temperature=3)).status_code == 400
    assert client.post("/api/ia/agents", json=agent_body(doc_ids=[999])).status_code == 400


def test_ia_separees_par_compte(site, client):
    a = client.post("/api/ia/agents", json=agent_body()).json()
    site.add_user()
    autre = site.login("alice", "alice-pass-123")
    assert autre.get("/api/ia/agents").json() == []
    assert autre.get(f"/api/ia/agents/{a['id']}").status_code == 404
    assert autre.post("/api/ia/chat", json={"target": f"agent:{a['id']}", "message": "x"}).status_code == 404


def test_erreur_d_indexation_visible(site, client, monkeypatch):
    def broken(texts):
        raise ConnectionError("modèle absent")
    monkeypatch.setattr(site.m.agents, "embed", broken)
    doc = add_ready_doc(site, "disques.md", DOC_SSD)
    a = client.post("/api/ia/agents", json=agent_body(doc_ids=[doc])).json()
    idx = client.get(f"/api/ia/agents/{a['id']}").json()["index"]
    assert idx["vectors"] == 0 and "modèle absent" in idx["errors"][0]


def test_publier_dans_ollama(site, client, monkeypatch):
    calls = []
    monkeypatch.setattr(site.m.agents, "ollama_json", lambda path, payload=None, **k: calls.append((path, payload)) or {})
    a = client.post("/api/ia/agents", json=agent_body(name="Expert Disques é")).json()
    r = client.post(f"/api/ia/agents/{a['id']}/publish").json()
    assert r["ollama_model"] == f"ia-{a['id']}-expert-disques-e" and r["published"]
    path, payload = calls[-1]
    assert path == "/api/create" and payload["from"] == "qwen3:4b-instruct"
    assert payload["parameters"] == {"temperature": 0.2} and "## Tâche" in payload["system"]
    client.delete(f"/api/ia/agents/{a['id']}")
    assert calls[-1] == ("/api/delete", {"model": r["ollama_model"]})


def test_publier_ollama_en_panne(site, client, monkeypatch):
    monkeypatch.setattr(site.m.agents, "ollama_json", lambda *a, **k: {"error": "pull model manifest"})
    a = client.post("/api/ia/agents", json=agent_body()).json()
    r = client.post(f"/api/ia/agents/{a['id']}/publish")
    assert r.status_code == 502 and "manifest" in r.json()["error"]


# ---------- Tchat ----------
def test_tchat_avec_une_ia_et_ses_documents(site, client, monkeypatch):
    monkeypatch.setattr(site.m.agents, "embed", fake_vectors)
    fake = fake_stream(["Rempla", "cez le SSD [1]"])
    monkeypatch.setattr(site.m.agents, "ollama_stream", fake)
    doc = add_ready_doc(site, "disques.md", DOC_SSD)
    a = client.post("/api/ia/agents", json=agent_body(doc_ids=[doc])).json()
    lines = ndjson_lines(client.post("/api/ia/chat", json={"target": f"agent:{a['id']}",
                                                          "message": "Le SSD Kingston va bien ?"}))
    head, done = lines[0], lines[-1]
    assert head["search"] == "vecteurs" and head["sources"][0]["title"] == "SSD système"
    assert "".join(x.get("t", "") for x in lines) == "Remplacez le SSD [1]"
    model, msgs = fake.calls[0][0], fake.calls[0][1]
    assert model == "qwen3:4b-instruct"
    assert msgs[0]["role"] == "system" and "## Contraintes\nPhrases simples" in msgs[0]["content"]
    assert "1 % de vie" in msgs[-1]["content"] and msgs[-1]["content"].endswith("Question : Le SSD Kingston va bien ?")
    # La conversation est enregistrée, sans les extraits dans le message de l'utilisateur
    conv = client.get(f"/api/ia/conversations/{done['conversation_id']}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][0]["content"] == "Le SSD Kingston va bien ?"
    assert conv["messages"][1]["meta"]["sources"][0]["doc"] == "disques.md"
    assert conv["title"] == "Le SSD Kingston va bien ?" and conv["target"] == f"agent:{a['id']}"


def test_recherche_par_mots_sans_vecteurs(site, client, monkeypatch):
    fake = fake_stream(["ok"])
    monkeypatch.setattr(site.m.agents, "ollama_stream", fake)
    doc = add_ready_doc(site, "disques.md", DOC_SSD)
    lines = ndjson_lines(client.post("/api/ia/chat", json={"target": "ollama:llama3.2:3b", "doc_ids": [doc],
                                                          "message": "Combien de nœuds Storj ?"}))
    assert lines[0]["search"] == "mots" and lines[0]["sources"][0]["title"] == "Disques de données"
    assert fake.calls[0][0] == "llama3.2:3b"
    assert fake.calls[0][1][0]["role"] == "system"  # consigne « cite les extraits »


def test_suite_de_conversation_et_regeneration(site, client, monkeypatch):
    fake = fake_stream(["Réponse"])
    monkeypatch.setattr(site.m.agents, "ollama_stream", fake)
    first = ndjson_lines(client.post("/api/ia/chat", json={"target": "ollama:qwen3:4b-instruct", "message": "A"}))
    cid = first[-1]["conversation_id"]
    client.post("/api/ia/chat", json={"target": "ollama:qwen3:4b-instruct", "conversation_id": cid, "message": "B"})
    assert [m["content"] for m in fake.calls[-1][1]] == ["A", "Réponse", "B"]
    client.post("/api/ia/chat", json={"target": "ollama:qwen3:4b-instruct", "conversation_id": cid,
                                      "regenerate": True})
    msgs = client.get(f"/api/ia/conversations/{cid}").json()["messages"]
    assert [m["content"] for m in msgs] == ["A", "Réponse", "B", "Réponse"]
    assert len(client.get("/api/ia/conversations").json()) == 1


def test_titre_sans_les_sections_du_prompteur(site, client, monkeypatch):
    monkeypatch.setattr(site.m.agents, "ollama_stream", fake_stream(["ok"]))
    msg = "## Contexte\nBlog homelab.\n## Tâche\nÉcris un plan."
    cid = ndjson_lines(client.post("/api/ia/chat", json={"target": "ollama:x", "message": msg}))[-1]["conversation_id"]
    assert client.get(f"/api/ia/conversations/{cid}").json()["title"] == "Blog homelab. Écris un plan."


def test_erreur_ollama_enregistree(site, client, monkeypatch):
    monkeypatch.setattr(site.m.agents, "ollama_stream", fake_stream(["début"], error=ConnectionError("coupé")))
    lines = ndjson_lines(client.post("/api/ia/chat", json={"target": "ollama:x", "message": "Bonjour"}))
    assert any("coupé" in x.get("error", "") for x in lines) and lines[-1]["done"] is True
    msgs = client.get(f"/api/ia/conversations/{lines[-1]['conversation_id']}").json()["messages"]
    assert msgs[1]["content"] == "début" and "coupé" in msgs[1]["meta"]["error"]


def test_tchat_claude_passe_par_la_cle_du_compte(site, client, monkeypatch):
    def claude(key, model_id, messages, state):
        claude.calls.append((key, model_id, messages))
        state["tin"], state["tout"] = 100, 50
        yield "Salut"
    claude.calls = []
    monkeypatch.setattr(site.m.chat, "claude_stream", claude)
    client.post("/api/chat/settings", json={"api_key": "sk-ant-test-ABCD"})
    lines = ndjson_lines(client.post("/api/ia/chat", json={"target": "claude:haiku", "message": "Bonjour"}))
    assert claude.calls[0][:2] == ("sk-ant-test-ABCD", "claude-haiku-5-5")
    assert lines[-1]["cost"] > 0 and lines[-1]["month_tokens"] == 150


def test_tchat_claude_plafond_avant_enregistrement(site, client):
    client.post("/api/chat/settings", json={"api_key": "sk-ant-test-ABCD", "cap": 1.0})
    with site.m.core.db() as con:
        con.execute("INSERT INTO chat_usage(date, user_id, model, tokens_in, tokens_out, cost) "
                    "VALUES (?, 1, 'x', 0, 0, 2)", (site.m.core.now_iso(),))
    r = client.post("/api/ia/chat", json={"target": "claude:haiku", "message": "Bonjour"})
    assert r.status_code == 402 and client.get("/api/ia/conversations").json() == []


def test_cibles_invalides(client):
    assert client.post("/api/ia/chat", json={"target": "gpt:4", "message": "x"}).status_code == 400
    assert client.post("/api/ia/chat", json={"target": "claude:opus", "message": "x"}).status_code == 400
    assert client.post("/api/ia/chat", json={"target": "ollama:x", "message": " "}).status_code == 400
    assert client.post("/api/ia/chat", json={"target": "ollama:x", "regenerate": True}).status_code == 400


def test_conversations_privees_renommer_supprimer(site, client, monkeypatch):
    monkeypatch.setattr(site.m.agents, "ollama_stream", fake_stream(["ok"]))
    cid = ndjson_lines(client.post("/api/ia/chat", json={"target": "ollama:x", "message": "Salut"}))[-1]["conversation_id"]
    site.add_user()
    autre = site.login("alice", "alice-pass-123")
    assert autre.get(f"/api/ia/conversations/{cid}").status_code == 404
    assert client.patch(f"/api/ia/conversations/{cid}", json={"title": "Renommée"}).json()["title"] == "Renommée"
    assert client.patch(f"/api/ia/conversations/{cid}", json={"target": "rien"}).status_code == 400
    client.delete(f"/api/ia/conversations/{cid}")
    assert client.get("/api/ia/conversations").json() == []


def test_document_supprime_retire_de_l_ia(site, client):
    doc = add_ready_doc(site, "disques.md", DOC_SSD)
    a = client.post("/api/ia/agents", json=agent_body(doc_ids=[doc])).json()
    client.delete(f"/api/library/{doc}")
    a = client.get(f"/api/ia/agents/{a['id']}").json()
    assert a["doc_ids"] == [] and a["index"]["chunks"] == 0


# ---------- Structurer ----------
def test_structurer_un_message(site, client, monkeypatch):
    fake = fake_stream(["```markdown\n## Contexte\nx\n```"])
    monkeypatch.setattr(site.m.agents, "ollama_stream", fake)
    lines = ndjson_lines(client.post("/api/ia/structure", json={"text": "Réparer mon SSD", "context_id": 1}))
    assert lines[-1]["prompt"] == "## Contexte\nx"
    assert fake.calls[0][0] == "prompteur-claude" and "Réparer mon SSD" in fake.calls[0][1][0]["content"]
    assert client.post("/api/ia/structure", json={"text": " "}).status_code == 400


def test_pages_ia_protegees(site):
    c = site.client()
    assert c.get("/ia.html").status_code == 303
    assert c.get("/api/ia/agents").status_code == 401


def test_pas_de_mes_ia_sur_le_tableau_de_bord(admin_site):
    admin_site.m.core.create_user("chef", "chef-pass-123", is_admin=True)
    c = admin_site.login("chef", "chef-pass-123")
    assert c.get("/api/ia/agents").status_code == 404
    assert c.get("/ia.html").headers["location"] == "/admin.html"
