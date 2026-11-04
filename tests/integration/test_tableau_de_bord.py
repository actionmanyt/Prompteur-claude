# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests d'intégration : tableau de bord (GPU, modèles chargés, santé, export dataset.jsonl)."""
import json
import subprocess
import types

import pytest

from conftest import add_ready_doc


@pytest.fixture
def admin(admin_site):
    admin_site.m.core.create_user("chef", "chef-pass-123", is_admin=True)
    return admin_site.login("chef", "chef-pass-123")


def fake_run(stdout="", exc=None):
    def run(cmd, **kwargs):
        run.calls.append(cmd)
        if exc:
            raise exc
        return types.SimpleNamespace(stdout=stdout)
    run.calls = []
    return run


def fake_ollama(routes):
    def ollama_json(path, payload=None, timeout=5):
        if path not in routes:
            raise ConnectionError("Ollama arrêté")
        r = routes[path]
        if isinstance(r, Exception):
            raise r
        return r
    return ollama_json


# ---------- GPU ----------
def test_gpu_lu_par_nvidia_smi(admin_site, admin, monkeypatch):
    run = fake_run("NVIDIA GeForce GTX 1060 6GB, 37, 3120, 6144, 54, 61.27, 120.00\n")
    monkeypatch.setattr(admin_site.m.admin.subprocess, "run", run)
    assert admin.get("/api/admin/gpu").json() == {
        "name": "NVIDIA GeForce GTX 1060 6GB", "util": 37, "mem_used": 3120, "mem_total": 6144,
        "temp": 54, "power": 61, "power_limit": 120}
    assert run.calls[0][0] == "nvidia-smi"


def test_gpu_absent_du_conteneur(admin_site, admin, monkeypatch):
    monkeypatch.setattr(admin_site.m.admin.subprocess, "run", fake_run(exc=FileNotFoundError()))
    r = admin.get("/api/admin/gpu")
    assert r.status_code == 503 and "pas donné au conteneur" in r.json()["error"]


def test_gpu_nvidia_smi_bloque(admin_site, admin, monkeypatch):
    monkeypatch.setattr(admin_site.m.admin.subprocess, "run",
                        fake_run(exc=subprocess.TimeoutExpired("nvidia-smi", 5)))
    assert admin.get("/api/admin/gpu").status_code == 503


def fake_uname(monkeypatch, admin_site):
    """os.uname n'existe pas sous Windows ; l'app, elle, tourne sous Linux."""
    u = types.SimpleNamespace(nodename="conteneur", machine="x86_64")
    monkeypatch.setattr(admin_site.m.admin.os, "uname", lambda: u, raising=False)


def test_machine_detectee(admin_site, admin, monkeypatch):
    fake_uname(monkeypatch, admin_site)
    monkeypatch.setattr(admin_site.m.admin.subprocess, "run", fake_run("NVIDIA GeForce GTX 1060 6GB, 6144\n"))
    monkeypatch.setenv("SERVER_NAME", "srv")
    s = admin.get("/api/admin/system").json()
    assert s["host"] == "srv" and s["gpus"] == [{"name": "GTX 1060 6GB", "vram_go": 6}]
    assert s["threads"] > 0


def test_machine_sans_gpu(admin_site, admin, monkeypatch):
    fake_uname(monkeypatch, admin_site)
    monkeypatch.setattr(admin_site.m.admin.subprocess, "run", fake_run(exc=FileNotFoundError()))
    assert admin.get("/api/admin/system").json()["gpus"] == []


# ---------- Modèles chargés ----------
def test_modeles_charges(admin_site, admin, monkeypatch):
    monkeypatch.setattr(admin_site.m.admin, "ollama_json", fake_ollama({"/api/ps": {"models": [
        {"name": "prompteur-claude:latest", "size_vram": 3_500_000_000, "expires_at": "2026-11-04T21:00:00Z",
         "digest": "ignoré"}]}}))
    assert admin.get("/api/admin/ps").json() == {"models": [
        {"name": "prompteur-claude:latest", "size_vram": 3_500_000_000, "expires_at": "2026-11-04T21:00:00Z"}]}


def test_modeles_ollama_injoignable(admin_site, admin, monkeypatch):
    monkeypatch.setattr(admin_site.m.admin, "ollama_json", fake_ollama({}))
    assert admin.get("/api/admin/ps").status_code == 503


# ---------- Santé ----------
def _health(admin_site, admin, monkeypatch, tags, site_state=("Up", "répond (HTTP 200)")):
    monkeypatch.setattr(admin_site.m.admin, "ping", lambda url: site_state)
    monkeypatch.setattr(admin_site.m.admin, "ollama_json", fake_ollama({
        "/api/version": {"version": "0.12.0"},
        "/api/tags": {"models": [{"name": t} for t in tags]},
        "/api/show": {"details": {"family": "qwen3", "parameter_size": "4.0B", "quantization_level": "Q4_K_M"}},
    }))
    r = admin.get("/api/admin/health").json()
    return r, {s["name"]: s for s in r["services"]}


def test_sante_tout_va_bien(admin_site, admin, monkeypatch):
    r, s = _health(admin_site, admin, monkeypatch, ["prompteur-claude:latest", "qwen3:4b-instruct"])
    assert r["version"] == "0.6.0" and r["git"] == "test"
    assert all(x["state"] == "Up" for x in r["services"]), r["services"]
    assert "qwen3 · 4.0B · Q4_K_M" in s["modèle prompteur-claude"]["detail"]


def test_sante_modele_absent(admin_site, admin, monkeypatch):
    _, s = _health(admin_site, admin, monkeypatch, ["qwen3:4b-instruct"])
    assert s["modèle prompteur-claude"]["state"] == "Erreur"


def test_sante_site_injoignable(admin_site, admin, monkeypatch):
    _, s = _health(admin_site, admin, monkeypatch, ["prompteur-claude:latest"],
                   site_state=("Erreur", "injoignable (URLError)"))
    assert s["md-converter (site du Prompteur)"]["state"] == "Erreur"


def test_sante_ollama_arrete(admin_site, admin, monkeypatch):
    monkeypatch.setattr(admin_site.m.admin, "ping", lambda url: ("Up", "ok"))
    monkeypatch.setattr(admin_site.m.admin, "ollama_json", fake_ollama({}))
    s = {x["name"]: x for x in admin.get("/api/admin/health").json()["services"]}
    assert s["ollama"]["state"] == "Erreur"
    assert s["base de données"]["state"] == "Up"


def test_sante_services_en_plus(admin_site, admin, monkeypatch):
    monkeypatch.setenv("HEALTH_URLS", "open-webui=http://open-webui:8080,n8n=http://n8n:5678")
    _, s = _health(admin_site, admin, monkeypatch, ["prompteur-claude:latest"])
    assert {"open-webui", "n8n"} <= set(s)


# ---------- Export du jeu de données ----------
def test_export_dataset_seulement_les_prompts_ideaux(admin_site, admin):
    core = admin_site.m.core
    doc = add_ready_doc(admin_site, "devis.md", "Prix : 100 €")
    with core.db() as con:
        for idea, ideal in (("Bonne idée", 1), ("Brouillon", 0)):
            hid = con.execute("INSERT INTO history(date, idea, ideal, prompt) VALUES (?,?,?,?)",
                              (core.now_iso(), idea, ideal, f"Prompt de {idea}")).lastrowid
            con.execute("INSERT INTO history_documents VALUES (?,?,?)", (hid, doc, "devis.md"))
    r = admin.get("/api/admin/dataset.jsonl")
    assert r.headers["content-disposition"] == 'attachment; filename="dataset.jsonl"'
    lines = [json.loads(x) for x in r.text.splitlines()]
    assert lines == [{"idea": "Bonne idée", "sources": ["devis.md"], "excerpt": "Prix : 100 €",
                      "prompt": "Prompt de Bonne idée"}]


def test_export_dataset_vide(admin):
    assert admin.get("/api/admin/dataset.jsonl").text == ""
