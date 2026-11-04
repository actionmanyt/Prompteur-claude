# Tests automatiques du Prompteur Claude

*Ajouté le 04/11/2026 (version 0.6.0).*

## Les trois suites

| Suite | Dossier | Ce qu'elle vérifie | Où la lancer |
|---|---|---|---|
| Unitaires | `tests/unit/` | Fonctions seules : tokens, mots de passe, chiffrement, allègement, sections, conversions, construction du message envoyé à Ollama | PC, GitHub Actions |
| Intégration | `tests/integration/` | Toutes les routes `/api/…` avec une base SQLite neuve : connexion, comptes et mot de passe provisoire, séparation 8093 / 8094, bibliothèque, budget de 6 992 tokens, génération, historique, tchat (clé, plafond, secours), tableau de bord, `dataset.jsonl` | PC, GitHub Actions |
| Live | `tests/live/` | L'installation réelle sur `srv` : pages, accès, santé des services, GPU, modèle `qwen3`, réactivité d'une vraie génération | PC (via Tailscale), après `./deploy.sh` |

Les suites unitaires et d'intégration n'appellent **jamais** Ollama, l'API Claude ni `nvidia-smi` : des imitations les remplacent. Elles ne touchent pas au dossier `data/`.

## Installation (PC, PowerShell, une seule fois)

```powershell
cd C:\Users\arthu\Documents\prompteur-claude
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

## Lancer les tests (PC, PowerShell)

```powershell
python tests/run_tests.py                       # unitaires + intégration
python tests/run_tests.py unit -k tokens        # un seul groupe de tests
python tests/run_tests.py live --url http://100.101.4.16:8093 --admin-url http://100.101.4.16:8094 --user arthur
python tests/run_tests.py live --url http://100.101.4.16:8093 --user arthur --generate   # + vraie génération
```

Le mot de passe est demandé au clavier (ou lu dans `PROMPTEUR_PASSWORD`). `--generate` ajoute une ligne « [test automatique] » à l'historique : la supprimer ensuite dans le tableau de bord.

## Rapport

Dossier `rapports-tests/` (ignoré par Git) :

- `dernier-rapport.md` : résultat, résumé par suite, échecs détaillés, tests ignorés et pourquoi, tests les plus lents ;
- `rapport-AAAAMMJJ-HHMMSS.md` : les 10 derniers ;
- `junit-*.xml` et `log-*.txt` : sorties brutes.

Codes de sortie : `0` tout passe, `1` échecs, `2` problème d'outillage (pytest absent, délai dépassé), `130` Ctrl+C.

## GitHub Actions

`.github/workflows/tests.yml` lance les suites unitaires et d'intégration à chaque push sur `main`, puis construit l'image Docker. Le rapport apparaît dans le résumé de l'exécution (onglet Actions) et en pièce jointe.

## Nouvelle routine de mise à jour

1. Sur le PC : modifier, `python tests/run_tests.py`, `git commit -s`, `git push`.
2. Sur le serveur : `cd /mnt/ssd/apps/md-converter/ && ./deploy.sh`.
3. Sur le PC : `python tests/run_tests.py live --url http://100.101.4.16:8093 --admin-url http://100.101.4.16:8094 --user arthur`.
4. Mettre à jour `docs/modifier-prompteur.md`.

## Ajouter un test

- Une fonction pure : dans `tests/unit/`, avec la fixture `core`, `site` ou `admin_site` pour obtenir les modules.
- Une route : dans `tests/integration/`, avec `client` (connecté en admin sur le site), `site.login(...)` ou `admin_site`.
- Imiter Ollama : `monkeypatch.setattr(site.m.prompts, "ollama_stream", fake_stream(["morceau 1", "morceau 2"]))`.
- Un document prêt sans conversion : `add_ready_doc(site, "nom.md", "texte")`.
