# Modifier et maintenir le Prompteur Claude

*Version 0.8.0 (05/11/2026) : prompts idéaux écrits ou corrigés à la main dans le tableau de bord. 0.7.0 : comptes gérés depuis le tableau de bord, mot de passe provisoire.*

## Où sont les choses

| Élément | Emplacement |
|---|---|
| Code (serveur) | `/mnt/ssd/apps/md-converter/`, relié au dépôt GitHub `actionmanyt/Prompteur-claude` |
| Code (PC) | `C:\Users\arthu\Documents\prompteur-claude\` |
| Données | `/mnt/ssd/apps/md-converter/data/` : `prompteur.db`, sauvegardes `prompteur.db.bak-*`, `tmp/` |
| Secrets | `/mnt/ssd/apps/md-converter/.env` (jamais dans Git) |
| Site du Prompteur | `http://100.101.4.16:8093/` (Utilisation, Tchat ; ancienne page : `/ancien`), conteneur `md-converter` |
| Tableau de bord | `http://100.101.4.16:8094/` (comptes admin uniquement), conteneur `md-admin` |

## Fichiers du code

| Fichier | Rôle |
|---|---|
| `app.py` | Démarrage, contrôle de connexion, séparation site (`APP_MODE=user`) / tableau de bord (`APP_MODE=admin`) |
| `core.py` | Réglages, base SQLite, mots de passe, chiffrement, appels Ollama |
| `auth.py` | `/api/login`, `/api/logout`, `/api/me`, `/api/me/password` (changer son mot de passe) |
| `accounts.py` | `/api/admin/users` : créer, nouveau mot de passe, rôle, déconnecter, supprimer (uniquement sur le port 8094) |
| `ideals.py` | `/api/admin/ideal` : ajouter, corriger ou modifier un prompt idéal à la main (uniquement sur le port 8094) |
| `convert.py` | Conversion des fichiers et pages web en Markdown, découpage en sections |
| `library.py` | `/api/library` (envoi, conversion en tâche de fond), `/api/contexts` |
| `prompts.py` | `/api/generate` (streaming), `/api/history` |
| `admin.py` | `/api/admin/system`, `/gpu`, `/ps`, `/health`, `/dataset.jsonl` (uniquement sur le port 8094) |
| `chat.py` | `/api/chat`, réglages et compteur de coût par compte |
| `legacy.py` | Ancienne page sur `/ancien`, à supprimer quand la nouvelle convient |
| `manage.py` | Comptes en ligne de commande (secours, si le tableau de bord est inaccessible) |
| `deploy.sh` | Sauvegarde de la base, `git pull`, reconstruction, vérification |

## Routine de modification

1. **PC** : modifier, puis `git commit -am "…"` et `git push`.
2. **Serveur** : `cd /mnt/ssd/apps/md-converter && ./deploy.sh`.
3. Ouvrir la page, `Ctrl+F5`, tester.
4. Mettre à jour ce fichier.

`deploy.sh` sauvegarde la base avant tout (7 dernières copies gardées).

## Deux adresses

Le même code tourne dans deux conteneurs qui partagent la base `data/prompteur.db` :

- **`md-converter` (8093)** : Utilisation et Tchat. Il ne contient pas l'Admin : `/admin.html` et `/api/admin/…` y répondent « introuvable », même pour un admin.
- **`md-admin` (8094)** : le tableau de bord, refusé à tout compte non admin. C'est lui qui reçoit le GPU (lecture de `nvidia-smi`).

La connexion est partagée entre les deux adresses (même machine) : se déconnecter sur l'une déconnecte de l'autre.

## Comptes

**Depuis le tableau de bord** (`http://100.101.4.16:8094/`, section **1.7 Comptes**) :

- **Créer un compte** : identifiant (2 à 32 caractères : lettres, chiffres, `.` `-` `_`), mot de passe (8 caractères minimum, bouton « Générer »), case « Administrateur » pour donner l'accès au tableau de bord.
- **Provisoire** (coché par défaut) : à sa première connexion, l'utilisateur doit choisir son propre mot de passe. Tant qu'il ne l'a pas fait, le serveur refuse toutes les autres routes.
- Après la création, l'adresse, l'identifiant et le mot de passe s'affichent une seule fois, avec un bouton « Copier » pour les transmettre.
- Par compte : **Mot de passe** (nouveau mot de passe, provisoire ou non ; ferme ses sessions), **Rendre admin / Retirer admin**, **Déconnecter** (ferme ses sessions), **Supprimer** (efface aussi sa clé API et ses réglages ; l'historique de ses prompts est gardé).
- Protections : impossible de se supprimer soi-même, de retirer ses propres droits, ou de supprimer le dernier administrateur.

**Pour chaque utilisateur** : lien **Mot de passe** dans le menu de toutes les pages. Le changement ferme ses autres sessions (téléphone, autre navigateur) et garde celle en cours.

**En ligne de commande** (serveur, en secours) :

```bash
docker exec -it md-converter python manage.py users
docker exec -it md-converter python manage.py adduser marie          # simple utilisateur
docker exec -it md-converter python manage.py adduser paul --admin   # accès au tableau de bord (8094)
docker exec -it md-converter python manage.py passwd marie           # mot de passe définitif, sessions fermées
docker exec -it md-converter python manage.py deluser marie
```

Le premier compte admin est créé au premier démarrage à partir de `ADMIN_USER` et `ADMIN_PASSWORD` dans `.env`. La base d'une version précédente est mise à jour automatiquement au démarrage (colonne `must_change`).

## Prompts idéaux (0.8.0)

Dans le tableau de bord (`http://100.101.4.16:8094/`, section **1.4 Historique**) :

- **+ Ajouter un prompt idéal** : écrire un prompt de zéro (idée, extrait du document facultatif, prompt). Le bouton **Gabarit** insère les 4 sections, et la fenêtre signale les sections absentes.
- **✎ Corriger en prompt idéal** (détail d'un prompt généré) : reprend l'idée, les documents liés et le prompt, sans les documents joints à la fin. La correction devient une nouvelle entrée ; l'original reste tel quel.
- **✎ Modifier** (détail d'une entrée manuelle) : change l'idée, l'extrait ou le prompt.

Les entrées manuelles ont le modèle `manuel`, sont marquées ★ et partent dans `dataset.jsonl`. L'extrait saisi y remplace celui des documents ; sans extrait, l'export reprend les sections gardées des documents liés.

Code : `ideals.py` (routes `/api/admin/ideal`, port 8094 seulement). Au démarrage, la table `history` reçoit les colonnes `excerpt` et `manual` si elles manquent.

## Clé API Claude

Chaque utilisateur saisit sa clé dans **Tchat Claude → Réglages API**. Elle est chiffrée dans la base avec `SECRET_KEY` (`.env`). Si `SECRET_KEY` change, les clés enregistrées sont à ressaisir. Le plafond mensuel est vérifié par le serveur. Sans clé, ou si l'API ne répond pas, Ollama (`qwen3:4b-instruct`) répond en secours.

Les identifiants des modèles (`claude-haiku-5-5`, `claude-sonnet-5-5`) peuvent être changés avec `CLAUDE_HAIKU` et `CLAUDE_SONNET` dans `.env`. Les tarifs sont réglables dans la page : vérifiez-les sur la page des tarifs d'Anthropic.

## Dépannage

| Problème | Vérification |
|---|---|
| Le site ne s'ouvre pas | `docker ps --filter name=md-converter`, puis `docker logs md-converter --tail 50` |
| Le tableau de bord ne s'ouvre pas | `docker logs md-admin --tail 50` ; un compte non admin est renvoyé à la connexion |
| Impossible de se connecter | Un admin donne un nouveau mot de passe dans **1.7 Comptes** ; sinon `docker exec -it md-converter python manage.py passwd <nom>` |
| « Changez d'abord votre mot de passe provisoire » | Normal : choisir son mot de passe dans la fenêtre qui s'ouvre, puis `Ctrl+F5` |
| « Aucune carte NVIDIA détectée » | Le bloc `deploy:` du service `md-admin` donne le GPU au conteneur ; vérifier `docker exec md-admin nvidia-smi` |
| « Ollama : demande refusée (HTTP …) » | La raison donnée par Ollama suit (0.7.1). Le conteneur joint Ollama, mais Ollama refuse la demande : lire aussi `docker logs <conteneur-ollama> --tail 40` juste après l'erreur |
| « Ollama : pas de réponse en … s » | Machine sans carte graphique ou document long : augmenter `OLLAMA_TIMEOUT` (secondes, 300 par défaut) dans `.env` ou dans le compose, ou garder moins de sections |
| « Prompteur injoignable » | `docker exec md-converter python -c "import urllib.request;print(urllib.request.urlopen('http://ia-ollama-1:11434/api/version').read())"` |
| Document « erreur » | Survol du statut pour le détail ; un redémarrage pendant une conversion demande de renvoyer le fichier |
| Revenir en arrière | `git log --oneline`, `git checkout <commit> -- .`, `./deploy.sh` ; base : copier un `data/prompteur.db.bak-*` sur `prompteur.db` conteneur arrêté |

## Compteur de tokens

Le compteur est une estimation : **2,5 caractères par token** (`CHARS_PER_TOKEN` dans `core.py`, `tokens()` dans `static/common.js`). Mesure du 05/11/2026 : un document français avec tableaux estimé 5 795 tokens avec l'ancienne règle (4 caractères) en faisait 9 935 pour `qwen3`, et Ollama refusait la demande. Au démarrage, les tokens des documents déjà en base sont recomptés.

Si Ollama refuse quand même (« request (… tokens) exceeds the available context size »), la page affiche le vrai compte et le pourcentage de sections à retirer.

## PC sans serveur (portable)

Pour faire tourner le Prompteur sur un PC Windows avec Docker Desktop et un conteneur `ollama` (port 11434 ouvert) :

1. Télécharger le dépôt (`git clone` ou ZIP), créer `data\` et `.env` (à partir de `.env.example`).
2. Créer le modèle : `ollama pull qwen3:4b-instruct`, puis `ollama create prompteur-claude -f Modelfile-prompteur-claude.txt`.
3. Lancer : `docker compose -f docker-compose.portable.yml up -d --build`. Site sur `http://localhost:8093`, tableau de bord sur `http://localhost:8094`.

Sans carte NVIDIA, Ollama tourne sur le processeur (environ 12 tokens lus par seconde sur un portable) : `OLLAMA_TIMEOUT=900` est déjà réglé dans ce compose, et il vaut mieux garder des documents courts. La base `data\` de ce PC est séparée de celle du serveur.

## Formats

Pris en charge : PDF texte, DOCX, PPTX, XLSX, XLS, MSG, EML, HTML, adresses web, TXT, MD, CSV, JSON, XML, YAML, code, EPUB, ZIP (contrôle de taille). Pas encore : DOC, PPT, ODT/ODS/ODP, RTF, images et PDF scannés (OCR), audio et vidéo.
