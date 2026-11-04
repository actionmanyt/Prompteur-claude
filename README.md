# Prompteur Claude

Outil auto-hébergé qui transforme une idée et un ou plusieurs documents en un prompt court et structuré (Contexte, Tâche, Contraintes, Format), prêt à coller dans Claude, en utilisant le moins de tokens possible.

## Contenu

| Élément | Rôle |
|---|---|
| `app.py` | Application FastAPI (version du serveur `srv`) |
| `Dockerfile`, `requirements.txt` | Construction du conteneur `md-converter` |
| `Modelfile-prompteur-claude.txt` | Modèle Ollama `prompteur-claude` (base `qwen3:4b-instruct`) |
| `static/` | Pages Utilisation, Admin et Tchat (servies sur `/test/`) |
| `docs/` | `modifier-prompteur.md` et notes du projet |

## Installations

- **Serveur** `srv` : `http://100.101.4.16:8093`, dossier `/mnt/ssd/apps/md-converter/`
- **PC Windows** : `http://localhost:8093`

## Mise à jour du serveur

```bash
cd /mnt/ssd/apps/md-converter
cp app.py app.py.bak
git pull
docker compose up -d --build md-converter
```

Les données (`data/prompteur.db`) ne sont pas dans le dépôt : elles restent sur le serveur et sont incluses dans le script de sauvegarde.
## Licence

AGPL-3.0 : gratuit et libre. Toute version modifiée, distribuée ou mise en ligne,
doit publier son code sous la même licence. Voir CONTRIBUTING.md pour proposer vos améliorations.