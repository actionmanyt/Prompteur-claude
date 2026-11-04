# Modifier et maintenir le Prompteur Claude

*Version 0.5.0 (04/11/2026) : vraies données, connexion, tchat Claude.*

## Où sont les choses

| Élément | Emplacement |
|---|---|
| Code (serveur) | `/mnt/ssd/apps/md-converter/`, relié au dépôt GitHub `actionmanyt/Prompteur-claude` |
| Code (PC) | `C:\Users\arthur.lejosne\Documents\Perso\Test\prompteur-claude\` |
| Données | `/mnt/ssd/apps/md-converter/data/` : `prompteur.db`, sauvegardes `prompteur.db.bak-*`, `tmp/` |
| Secrets | `/mnt/ssd/apps/md-converter/.env` (jamais dans Git) |
| Page | `http://100.101.4.16:8093/` (ancienne page : `/ancien`) |

## Fichiers du code

| Fichier | Rôle |
|---|---|
| `app.py` | Démarrage, contrôle de connexion sur toutes les pages, montage de `static/` |
| `core.py` | Réglages, base SQLite, mots de passe, chiffrement, appels Ollama |
| `auth.py` | `/api/login`, `/api/logout`, `/api/me` |
| `convert.py` | Conversion des fichiers et pages web en Markdown, découpage en sections |
| `library.py` | `/api/library` (envoi, conversion en tâche de fond), `/api/contexts` |
| `prompts.py` | `/api/generate` (streaming), `/api/history` |
| `admin.py` | `/api/admin/gpu`, `/ps`, `/health`, `/dataset.jsonl` (réservé à l'admin) |
| `chat.py` | `/api/chat`, réglages et compteur de coût par compte |
| `legacy.py` | Ancienne page sur `/ancien`, à supprimer quand la nouvelle convient |
| `manage.py` | Comptes en ligne de commande |
| `deploy.sh` | Sauvegarde de la base, `git pull`, reconstruction, vérification |

## Routine de modification

1. **PC** : modifier, puis `git commit -am "…"` et `git push`.
2. **Serveur** : `cd /mnt/ssd/apps/md-converter && ./deploy.sh`.
3. Ouvrir la page, `Ctrl+F5`, tester.
4. Mettre à jour ce fichier.

`deploy.sh` sauvegarde la base avant tout (7 dernières copies gardées).

## Comptes (serveur)

```bash
docker exec -it md-converter python manage.py users
docker exec -it md-converter python manage.py adduser marie          # simple utilisateur
docker exec -it md-converter python manage.py adduser paul --admin   # accès à la page Admin
docker exec -it md-converter python manage.py passwd marie
docker exec -it md-converter python manage.py deluser marie
```

Le premier compte admin est créé au premier démarrage à partir de `ADMIN_USER` et `ADMIN_PASSWORD` dans `.env`.

## Clé API Claude

Chaque utilisateur saisit sa clé dans **Tchat Claude → Réglages API**. Elle est chiffrée dans la base avec `SECRET_KEY` (`.env`). Si `SECRET_KEY` change, les clés enregistrées sont à ressaisir. Le plafond mensuel est vérifié par le serveur. Sans clé, ou si l'API ne répond pas, Ollama (`qwen3:4b-instruct`) répond en secours.

Les identifiants des modèles (`claude-haiku-5-5`, `claude-sonnet-5-5`) peuvent être changés avec `CLAUDE_HAIKU` et `CLAUDE_SONNET` dans `.env`. Les tarifs sont réglables dans la page : vérifiez-les sur la page des tarifs d'Anthropic.

## Dépannage

| Problème | Vérification |
|---|---|
| La page ne s'ouvre pas | `docker ps --filter name=md-converter`, puis `docker logs md-converter --tail 50` |
| Impossible de se connecter | `docker exec -it md-converter python manage.py users`, puis `passwd` |
| « GPU injoignable » dans l'Admin | Le bloc `deploy:` du `docker-compose.yml` donne le GPU au conteneur ; vérifier `docker exec md-converter nvidia-smi` |
| « Prompteur injoignable » | `docker exec md-converter python -c "import urllib.request;print(urllib.request.urlopen('http://ia-ollama-1:11434/api/version').read())"` |
| Document « erreur » | Survol du statut pour le détail ; un redémarrage pendant une conversion demande de renvoyer le fichier |
| Revenir en arrière | `git log --oneline`, `git checkout <commit> -- .`, `./deploy.sh` ; base : copier un `data/prompteur.db.bak-*` sur `prompteur.db` conteneur arrêté |

## Formats

Pris en charge : PDF texte, DOCX, PPTX, XLSX, XLS, MSG, EML, HTML, adresses web, TXT, MD, CSV, JSON, XML, YAML, code, EPUB, ZIP (contrôle de taille). Pas encore : DOC, PPT, ODT/ODS/ODP, RTF, images et PDF scannés (OCR), audio et vidéo.
