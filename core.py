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
RESERVE = 1200  # place gardée pour l'idée, le contexte et la consigne du Modelfile
DOC_BUDGET = NUM_CTX - RESERVE  # 6992 tokens de documents au maximum
APP_VERSION = "0.6.0"
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
  is_admin INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
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
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tokens(text: str) -> int:
    """Estimation grossière : environ 4 caractères par token."""
    return max(1, len(text or "") // 4) if text else 0


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


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    except Exception:
        return False
    return hmac.compare_digest(calc.hex(), h)


def create_user(username: str, password: str, is_admin: bool = False) -> int:
    with db() as con:
        cur = con.execute(
            "INSERT INTO users(username, pw_hash, is_admin, created) VALUES (?,?,?,?)",
            (username.strip(), hash_pw(password), int(is_admin), now_iso()))
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
def ollama_json(path: str, payload: dict | None = None, timeout: int = 5):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(OLLAMA_URL + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def ollama_stream(model: str, messages: list, timeout: int = 300):
    """Envoie une conversation à Ollama et renvoie le texte morceau par morceau."""
    payload = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            if not line.strip():
                continue
            j = json.loads(line)
            if j.get("error"):
                raise RuntimeError(j["error"])
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
