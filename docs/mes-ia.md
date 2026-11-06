# Mes IA et nouveau tchat

*Ajouté le 06/11/2026 (version 0.9.0, phase 1).*

## Principe

Une **IA** = un modèle Ollama de base + un rôle en 4 sections (Contexte, Tâche, Contraintes, Format) + des documents de la bibliothèque.

- Les poids du modèle ne changent pas (le vrai fine-tuning LoRA sera la phase 2).
- Les documents sont découpés en extraits d'environ 300 tokens, transformés en vecteurs par `bge-m3` (multilingue, bon en français), et rangés dans `data/prompteur.db` (table `chunks`).
- À chaque question, les 4 extraits les plus proches (`RAG_TOP_K`) sont ajoutés au message, et l'IA les cite par leur numéro ([1], [2]…).
- Sans modèle de vecteurs, ou s'il est absent, une recherche par mots prend le relais : ça marche, en moins précis.
- **Publier dans Ollama** crée un vrai modèle `ia-<id>-<nom>` (modèle de base + rôle, sans les documents), utilisable aussi dans Open WebUI.

## Pages

| Page | Rôle |
|---|---|
| `ia.html` (Mes IA) | Créer une IA : nom, modèle de base, température, 4 sections (ou « Remplir avec le Prompteur » à partir d'une phrase), documents, état de l'indexation, publication dans Ollama |
| `chat.html` (Tchat) | Conversations enregistrées par compte (barre de gauche), choix de la cible en haut (Mes IA, modèles Ollama, Claude), 📎 documents joints, ✨ Structurer (Prompteur), Arrêter, Régénérer, Copier, sources de chaque réponse. Les réglages Claude (clé, plafond, tarifs) sont dans ⚙ en bas à gauche |

## Installation sur `srv` (terminal SSH)

```bash
cd /mnt/ssd/apps/md-converter
cp app.py app.py.bak
git pull
docker compose up -d --build md-converter
```

`bge-m3` est déjà installé sur `srv` (1,2 Go). S'il manque : `docker exec -it ia-ollama-1 ollama pull bge-m3`. Ollama le charge à côté du modèle de tchat : vérifier la VRAM avec `nvidia-smi` si `OLLAMA_MAX_LOADED_MODELS=1` (il déchargera alors le modèle de tchat à chaque indexation). Les modèles Ollama sont sur le disque dur `sde` depuis le 06/11 : le premier chargement d'un modèle prend environ 30 s.

Les nouvelles tables (`agents`, `agent_documents`, `chunks`, `conversations`, `messages`) sont créées au démarrage. Aucune ancienne table n'est modifiée : retour arrière possible avec l'ancien `app.py` et l'ancienne image.

## Réglages (`docker-compose.yml`, section `environment`)

| Variable | Défaut | Rôle |
|---|---|---|
| `EMBED_MODEL` | `bge-m3` | Modèle Ollama qui calcule les vecteurs (changer de modèle oblige à tout réindexer, bouton ↻ Réindexer) |
| `RAG_TOP_K` | `4` | Nombre d'extraits ajoutés à chaque question |
| `NUM_CTX` | `8192` | Taille du contexte envoyée à Ollama : l'historique trop ancien est retiré pour tenir dedans |

## Routes

Toutes sous `/api/ia/`, réservées au compte connecté, sur le site (8093) seulement :
`models`, `agents` (+ `/{id}/index`, `/{id}/publish`), `conversations`, `chat` (streaming NDJSON), `structure`.
L'ancienne route `/api/chat` (Claude) ne change pas.
