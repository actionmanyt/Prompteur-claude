# SPDX-License-Identifier: AGPL-3.0-or-later
"""Page Admin : GPU, modèles chargés, santé des services, export du jeu de données."""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from core import APP_VERSION, DB_PATH, GIT_COMMIT, OLLAMA_URL, PROMPT_MODEL, SITE_URL, STARTED, db, ollama_json
from library import kept_text
from prompts import history_rows

router = APIRouter(prefix="/api/admin")
GPU_FIELDS = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit"


def num(v: str) -> float:
    try:
        return float(v)
    except ValueError:
        return 0.0


def duration(sec: float) -> str:
    sec = int(sec)
    if sec >= 86400:
        return f"{sec // 86400} j {sec % 86400 // 3600} h"
    if sec >= 3600:
        return f"{sec // 3600} h {sec % 3600 // 60} min"
    return f"{sec // 60} min"


@router.get("/gpu")
def gpu():
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={GPU_FIELDS}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, check=True).stdout
    except FileNotFoundError:
        return JSONResponse({"error": "nvidia-smi absent : le GPU n'est pas donné au conteneur"}, 503)
    except Exception as e:
        return JSONResponse({"error": f"nvidia-smi : {e}"}, 503)
    f = [x.strip() for x in out.strip().splitlines()[0].split(",")]
    return {"name": f[0], "util": round(num(f[1])), "mem_used": round(num(f[2])), "mem_total": round(num(f[3])),
            "temp": round(num(f[4])), "power": round(num(f[5])), "power_limit": round(num(f[6]))}


def short_cpu(name: str) -> str:
    """« Intel(R) Xeon(R) CPU E5-2670 v3 @ 2.30GHz » -> « Intel Xeon E5-2670 v3 »."""
    name = re.sub(r"\((R|TM|tm|r)\)|\bCPU\b|\bProcessor\b|@.*$|\d+-Core", "", name)
    return re.sub(r"\s+", " ", name).strip()


def short_gpu(name: str) -> str:
    """« NVIDIA GeForce GTX 1060 6GB » -> « GTX 1060 6GB »."""
    return re.sub(r"^(NVIDIA\s+)?(GeForce\s+)?", "", name.strip()) or name


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


@router.get("/system")
def system():
    """Ce qui est détecté sur la machine : nom, processeur, mémoire, cartes NVIDIA."""
    host = os.environ.get("SERVER_NAME", "").strip() or os.uname().nodename
    cpuinfo = read("/proc/cpuinfo")
    m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M) or re.search(r"^Model\s*:\s*(.+)$", cpuinfo, re.M)
    cpu = short_cpu(m.group(1)) if m else (os.uname().machine or "processeur inconnu")
    threads = os.cpu_count() or 0
    mem = re.search(r"^MemTotal:\s*(\d+)", read("/proc/meminfo"), re.M)
    ram_go = round(int(mem.group(1)) / 1024 / 1024) if mem else 0
    gpus = []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, check=True).stdout
        for line in filter(None, out.strip().splitlines()):
            name, _, vram = line.partition(",")
            gpus.append({"name": short_gpu(name), "vram_go": round(num(vram.strip()) / 1024)})
    except Exception:
        pass
    return {"host": host, "cpu": cpu, "threads": threads, "ram_go": ram_go, "gpus": gpus}


@router.get("/ps")
def ps():
    try:
        j = ollama_json("/api/ps")
    except Exception as e:
        return JSONResponse({"error": f"Ollama injoignable : {e}"}, 503)
    return {"models": [{"name": m.get("name"), "size_vram": m.get("size_vram", 0), "expires_at": m.get("expires_at")}
                       for m in j.get("models", [])]}


def ping(url: str) -> tuple[str, str]:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return ("Up", f"répond (HTTP {r.status})")
    except urllib.error.HTTPError as e:
        return ("Up", f"répond (HTTP {e.code})") if e.code < 500 else ("Erreur", f"HTTP {e.code}")
    except Exception as e:
        return ("Erreur", f"injoignable ({type(e).__name__})")


@router.get("/health")
def health():
    services = [{"name": "md-admin (ce tableau de bord)", "state": "Up", "detail": "Up depuis " + duration(time.time() - STARTED)}]
    state, detail = ping(SITE_URL.rstrip("/") + "/login.html")
    services.append({"name": "md-converter (site du Prompteur)", "state": state, "detail": detail})
    try:
        v = ollama_json("/api/version")
        services.append({"name": "ollama", "state": "Up", "detail": f"version {v.get('version', '?')} · {OLLAMA_URL}"})
        tags = [m.get("name", "") for m in ollama_json("/api/tags").get("models", [])]
        name = PROMPT_MODEL if ":" in PROMPT_MODEL else PROMPT_MODEL + ":latest"
        if name in tags:
            try:
                d = ollama_json("/api/show", {"model": PROMPT_MODEL}).get("details", {})
                detail = "présent · " + " · ".join(x for x in (d.get("family"), d.get("parameter_size"),
                                                               d.get("quantization_level")) if x)
            except Exception:
                detail = "présent"
            services.append({"name": f"modèle {PROMPT_MODEL}", "state": "Up", "detail": detail})
        else:
            services.append({"name": f"modèle {PROMPT_MODEL}", "state": "Erreur", "detail": "absent de ollama list"})
    except Exception as e:
        services.append({"name": "ollama", "state": "Erreur", "detail": f"injoignable ({type(e).__name__})"})
    try:
        with db() as con:
            n_docs = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            n_hist = con.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        size = os.path.getsize(DB_PATH) / 1e6
        services.append({"name": "base de données", "state": "Up",
                         "detail": f"{n_docs} documents · {n_hist} prompts · {size:.1f} Mo"})
    except Exception as e:
        services.append({"name": "base de données", "state": "Erreur", "detail": str(e)})
    # Services en plus, par ex. HEALTH_URLS=open-webui=http://open-webui:8080,n8n=http://n8n:5678
    for item in filter(None, os.environ.get("HEALTH_URLS", "").split(",")):
        name, _, url = item.partition("=")
        state, detail = ping(url.strip())
        services.append({"name": name.strip(), "state": state, "detail": detail})
    return {"services": services, "version": APP_VERSION, "git": GIT_COMMIT}


@router.get("/dataset.jsonl")
def dataset():
    lines = []
    with db() as con:
        for h in history_rows(con, "WHERE ideal = 1"):
            excerpts = []
            for doc_id in h["doc_ids"]:
                r = con.execute("SELECT sections FROM documents WHERE id=?", (doc_id,)).fetchone()
                if r:
                    excerpts.append(kept_text(json.loads(r["sections"] or "[]"))[:4000])
            lines.append(json.dumps({"idea": h["idea"], "sources": h["source"], "excerpt": "\n\n".join(excerpts),
                                     "prompt": h["prompt"]}, ensure_ascii=False))
    return Response("\n".join(lines) + ("\n" if lines else ""), media_type="application/jsonl",
                    headers={"Content-Disposition": 'attachment; filename="dataset.jsonl"'})
