# SPDX-License-Identifier: AGPL-3.0-or-later
"""Réglages, base SQLite, mots de passe, chiffrement et appel à Ollama."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "prompteur.db")
TMP_DIR = os.path.join(DATA_DIR, "tmp")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ia-ollama-1:11434").rstrip("/")
PROMPT_MODEL = os.environ.get("PROMPT_MODEL", "prompteur-claude")
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "qwen3:4b-instruct")
NUM_CTX = int(os.environ.get("NUM_CTX", "8192"))
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "300"))  # secondes d'attente d'une réponse d'Ollama
RESERVE = 1200  # place gardée pour l'idée, le contexte et la consigne du Modelfile
DOC_BUDGET = NUM_CTX - RESERVE  # 6992 tokens de documents au maximum
APP_VERSION = "0.9.0"
# Mes IA (0.9.0) : modèle de vecteurs pour chercher dans les documents, nombre d'extraits par question
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")  # multilingue, déjà installé sur srv
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "4"))
# « user » : site du Prompteur (8093) · « admin » : tableau de bord, sur sa propre adresse (8094)
APP_MODE = os.environ.get("APP_MODE", "user").strip().lower()
SITE_URL = os.environ.get("SITE_URL", "http://md-converter:8000")  # site vu depuis le conteneur admin
GIT_COMMIT = os.environ.get("GIT_COMMIT", "inconnu")
STARTED = time.time()
CONTEXT_FILE = os.environ.get("CONTEXT_FILE", os.path.join(DATA_DIR, "context.md"))

DEFAULT_CONTEXT = """- Serveur maison sous Ubuntu Server : Xeon E5-2670 v3, 32 Go RAM ECC, GTX 1060 6 Go
- Services en Docker : Portainer, Gitea, Ollama, Open WebUI, Paperless, n8n, Vaultwarden…
- Mini-PC Shuttle DS77
- Imprimante 3D Creality Ender 3 Pro V2
- Je suis en France. Réponses en français, concrètes, étape par étape."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, must_change INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT, date TEXT NOT NULL, size INTEGER DEFAULT 0,
  tokens INTEGER DEFAULT 0, tokens_full INTEGER DEFAULT 0, tags TEXT DEFAULT '[]',
  status TEXT NOT NULL, error TEXT, markdown TEXT, sections TEXT DEFAULT '[]', source_url TEXT);
CREATE TABLE IF NOT EXISTS contexts(id INTEGER PRIMARY KEY, name TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history(
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, user_id INTEGER, idea TEXT, context_id INTEGER,
  attach INTEGER DEFAULT 1, tokens_before INTEGER, tokens_after INTEGER, duration_s REAL,
  model TEXT, ideal INTEGER DEFAULT 0, prompt TEXT);
CREATE TABLE IF NOT EXISTS history_documents(
  history_id INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,
  document_id INTEGER NOT NULL, document_name TEXT, PRIMARY KEY(history_id, document_id));
CREATE TABLE IF NOT EXISTS chat_usage(
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, user_id INTEGER, model TEXT,
  tokens_in INTEGER, tokens_out INTEGER, cost REAL);
CREATE TABLE IF NOT EXISTS user_settings(
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, api_key_enc TEXT,
  cap REAL DEFAULT 5, default_model TEXT DEFAULT 'haiku', prices TEXT);
-- 0.9.0 : Mes IA (modèle Ollama + rôle en 4 sections + documents), extraits indexés, conversations
CREATE TABLE IF NOT EXISTS agents(
  id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, base_model TEXT NOT NULL, context TEXT DEFAULT '', task TEXT DEFAULT '',
  constraints TEXT DEFAULT '', format TEXT DEFAULT '', temperature REAL DEFAULT 0.4,
  ollama_model TEXT, published TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_documents(
  agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  PRIMARY KEY(agent_id, document_id));
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL, doc_hash TEXT NOT NULL, title TEXT, text TEXT NOT NULL,
  embed_model TEXT, vector BLOB);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id);
CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL, target TEXT NOT NULL, doc_ids TEXT DEFAULT '[]', created TEXT NOT NULL,
  updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL, content TEXT NOT NULL, meta TEXT DEFAULT '{}', date TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tokens(text: str) -> int:
    """Estimation prudente : 2,5 caractères par token, arrondi au-dessus.

    0.7.1 : l'ancienne règle (4 caractères) sous-estimait qwen3 sur du français : un document
    compté 5 795 tokens en faisait 9 935 pour Ollama.
    """
    return (len(text) * 2 + 4) // 5 if text else 0


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


# ---------- Mots de passe ----------
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


PW_MIN, PW_MAX = 8, 256
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,32}$")


def password_problem(pw: str) -> str | None:
    """Renvoie la raison du refus, ou None si le mot de passe convient."""
    if len(pw) < PW_MIN:
        return f"{PW_MIN} caractères minimum."
    if len(pw) > PW_MAX:
        return f"{PW_MAX} caractères maximum."
    if pw.strip() != pw:
        return "Pas d'espace au début ni à la fin."
    return None


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    except Exception:
        return False
    return hmac.compare_digest(calc.hex(), h)


def create_user(username: str, password: str, is_admin: bool = False, must_change: bool = False) -> int:
    with db() as con:
        cur = con.execute(
            "INSERT INTO users(username, pw_hash, is_admin, created, must_change) VALUES (?,?,?,?,?)",
            (username.strip(), hash_pw(password), int(is_admin), now_iso(), int(must_change)))
        return cur.lastrowid


# ---------- Chiffrement de la clé API ----------
def _fernet():
    from cryptography.fernet import Fernet
    secret = os.environ.get("SECRET_KEY", "").strip()
    if not secret:
        path = os.path.join(DATA_DIR, "secret.key")
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write(secrets.token_urlsafe(48))
            os.chmod(path, 0o600)
        with open(path) as f:
            secret = f.read().strip()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


# ---------- Ollama ----------
def ollama_json(path: str, payload: dict | None = None, timeout: int = 5, method: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(OLLAMA_URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class OllamaError(RuntimeError):
    """Refus ou silence d'Ollama, avec la raison qu'il donne (affichée telle quelle sur la page)."""


def _http_reason(e: urllib.error.HTTPError) -> str:
    try:
        raw = e.read().decode("utf-8", "replace").strip()
    except Exception:
        raw = ""
    try:
        return json.loads(raw).get("error") or raw
    except (ValueError, AttributeError):
        return raw or e.reason or ""


def ollama_stream(model: str, messages: list, timeout: int | None = None, options: dict | None = None):
    """Envoie une conversation à Ollama et renvoie le texte morceau par morceau.

    Lève OllamaError avec la raison d'Ollama (HTTP 400 « context length »…) ou le délai dépassé.
    """
    timeout = timeout or OLLAMA_TIMEOUT
    body = {"model": model, "messages": messages, "stream": True}
    if options:
        body["options"] = options
    payload = json.dumps(body).encode()
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise OllamaError(f"demande refusée (HTTP {e.code}) : {_http_reason(e)}") from e
    except TimeoutError as e:
        raise OllamaError(f"pas de réponse après {timeout} s (réglage OLLAMA_TIMEOUT)") from e
    with r:
        for line in r:
            if not line.strip():
                continue
            j = json.loads(line)
            if j.get("error"):
                raise OllamaError(j["error"])
            chunk = (j.get("message") or {}).get("content", "")
            if chunk:
                yield chunk
            if j.get("done"):
                break


def strip_fence(text: str) -> str:
    return re.sub(r"^`{3}[a-zA-Z]*\n|\n`{3}$", "", text.strip()).strip()


def ndjson(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


# ---------- Démarrage ----------
def migrate(con):
    """Mises à jour d'une base créée par une version précédente (chaque étape peut être refaite)."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "must_change" not in cols:  # 0.7.0 : mot de passe provisoire à changer
        try:
            con.execute("ALTER TABLE users ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):  # l'autre conteneur l'a ajoutée entre-temps
                raise
    recount_tokens(con)


def recount_tokens(con):
    """0.7.1 : recompte les documents avec la règle actuelle de tokens().
    Seules les lignes dont le compte change sont réécrites (rien d'écrit aux démarrages suivants)."""
    rows = con.execute("SELECT id, markdown, sections, tokens, tokens_full FROM documents "
                       "WHERE status='prêt' AND markdown IS NOT NULL").fetchall()
    for r in rows:
        try:
            secs = json.loads(r["sections"] or "[]")
        except ValueError:
            continue
        kept = tokens("\n\n".join(s.get("text", "") for s in secs if s.get("on")))
        full = tokens(r["markdown"])
        if (kept, full) != (r["tokens"], r["tokens_full"]):
            con.execute("UPDATE documents SET tokens=?, tokens_full=? WHERE id=?", (kept, full, r["id"]))


def init_db():
    """Lancé par les deux conteneurs (site et admin) : chaque étape supporte d'être faite deux fois."""
    os.makedirs(TMP_DIR, exist_ok=True)
    # Les deux conteneurs démarrent en même temps : si la base est occupée, on réessaie
    for attempt in range(30):
        try:
            with db() as con:
                if APP_MODE != "admin":
                    con.execute("PRAGMA journal_mode=WAL")
                con.executescript(SCHEMA)
                migrate(con)
            break
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == 29:
                raise
            time.sleep(1)
    if APP_MODE == "admin":
        return
    with db() as con:
        # Conversions coupées par un redémarrage du conteneur (seul le site convertit)
        con.execute("UPDATE documents SET status='erreur', error='Conversion interrompue (redémarrage) : renvoyez le fichier.' "
                    "WHERE status='en cours'")
        con.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        # Reprise de l'ancien contexte enregistré (context.md)
        if not con.execute("SELECT 1 FROM contexts LIMIT 1").fetchone():
            try:
                with open(CONTEXT_FILE, encoding="utf-8") as f:
                    text = f.read().strip() or DEFAULT_CONTEXT
            except FileNotFoundError:
                text = DEFAULT_CONTEXT
            con.execute("INSERT INTO contexts(name, text) VALUES (?, ?)", ("Mon contexte", text))
        has_user = con.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    # Premier compte admin, lu dans .env
    if not has_user:
        user = os.environ.get("ADMIN_USER", "").strip()
        pw = os.environ.get("ADMIN_PASSWORD", "")
        if user and pw:
            try:
                create_user(user, pw, is_admin=True)
                print(f"[prompteur] Compte admin « {user} » créé.", flush=True)
            except sqlite3.IntegrityError:
                pass  # déjà créé
        else:
            print("[prompteur] Aucun compte : renseignez ADMIN_USER et ADMIN_PASSWORD dans .env, "
                  "ou lancez « python manage.py adduser <nom> --admin ».", flush=True)
