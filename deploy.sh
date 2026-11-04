#!/usr/bin/env bash
# Mise à jour du Prompteur sur le serveur : sauvegarde de la base, git pull, reconstruction, vérification.
# Usage (sur le serveur) : cd /mnt/ssd/apps/md-converter && ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Fichier .env absent : cp .env.example .env, puis remplissez-le." >&2
  exit 1
fi

# 1. Sauvegarde de la base (copie cohérente, même conteneur allumé)
if docker ps --format '{{.Names}}' | grep -qx md-converter && [ -f data/prompteur.db ]; then
  stamp=$(date +%Y%m%d-%H%M)
  docker exec md-converter python -c "import sqlite3; s=sqlite3.connect('/data/prompteur.db'); d=sqlite3.connect('/data/prompteur.db.bak-$stamp'); s.backup(d); d.close()"
  echo "Base sauvegardée : data/prompteur.db.bak-$stamp"
  # Garde les 7 dernières (le dossier data/ appartient à l'utilisateur du conteneur)
  docker exec md-converter sh -c 'ls -1t /data/prompteur.db.bak-* | tail -n +8 | xargs -r rm --' || true
fi

# 2. Code à jour
git pull --ff-only

# 3. Reconstruction avec le numéro de commit affiché dans l'Admin
GIT_COMMIT=$(git rev-parse --short HEAD) SERVER_NAME=$(hostname) docker compose up -d --build

# 4. Vérifications
sleep 5
docker ps --filter name=md-converter --format 'Conteneur : {{.Names}} {{.Status}}'
curl -fsS -o /dev/null -w "Page de connexion : HTTP %{http_code}\n" http://localhost:8093/login.html \
  || { echo "La page ne répond pas : docker logs md-converter --tail 50" >&2; exit 1; }
