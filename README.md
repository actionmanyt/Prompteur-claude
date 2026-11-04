# Prompteur Claude

Transformez une idée et vos documents en un prompt court et structuré, prêt à coller dans Claude, en utilisant le moins de tokens possible.

Prompteur Claude est un outil **auto-hébergé**, **libre** et **gratuit**. La rédaction des prompts est confiée à une IA locale (Ollama) : vos documents restent chez vous, sans abonnement et sans connexion obligatoire à un service extérieur.

## Pourquoi ce projet ?

Coller un document entier dans Claude coûte cher en tokens, et un prompt mal organisé donne des réponses approximatives. Prompteur Claude allège vos documents, ne garde que l'utile, et produit un prompt toujours structuré en quatre parties :

- **Contexte** : un résumé de vos documents et de votre situation ;
- **Tâche** : ce que vous attendez de Claude ;
- **Contraintes** : les règles à respecter ;
- **Format** : la forme de la réponse.

## Fonctionnalités

**Utilisation**

- Bibliothèque de documents par glisser-déposer : PDF, Word, PowerPoint, Excel, HTML, pages web.
- Conversion en Markdown allégé, choix des sections à garder, compteur de tokens.
- Prompt basé sur un ou plusieurs documents, avec un budget de tokens contrôlé pour ne jamais dépasser la mémoire du modèle.
- À partir de deux documents, le prompt demande de citer la source et de signaler les contradictions.
- Contextes enregistrés, réutilisables d'un prompt à l'autre.
- Affichage du prompt au fur et à mesure de sa rédaction.

**Tchat Claude (optionnel)**

- Envoi direct du prompt à Claude avec votre propre clé API, stockée chiffrée et jamais réaffichée.
- Coût estimé par message, cumul mensuel et plafond de dépense.
- Réponse de secours par l'IA locale si l'API ne répond pas.

**Tableau de bord (administrateurs)**

- État de la carte graphique et des modèles chargés.
- Santé des services.
- Historique des prompts, avec « Régénérer » et « Marquer comme prompt idéal ».
- Export des prompts idéaux en `dataset.jsonl`, pour améliorer le modèle.
- Gestion des comptes : création, mot de passe provisoire, rôles, suppression.

## Prérequis

- [Docker](https://docs.docker.com/) et Docker Compose.
- [Ollama](https://github.com/ollama/ollama) avec le modèle de base `qwen3:4b-instruct`.
- Recommandé : une carte graphique NVIDIA avec 6 Go de mémoire vidéo. Sans carte graphique, l'outil fonctionne, mais la rédaction est plus lente.

## Installation

1. **Récupérer le code**

   ```bash
   git clone https://github.com/actionmanyt/Prompteur-claude.git
   cd Prompteur-claude
   ```

2. **Créer le modèle Ollama**

   ```bash
   ollama pull qwen3:4b-instruct
   ollama create prompteur-claude -f Modelfile-prompteur-claude.txt
   ```

   Si Ollama tourne dans Docker, lancez ces commandes avec `docker exec` dans son conteneur.

3. **Renseigner les secrets** dans un fichier `.env`, à la racine du dépôt :

   ```
   ADMIN_USER=votre-identifiant
   ADMIN_PASSWORD=un-mot-de-passe-solide
   SECRET_KEY=une-longue-chaine-aleatoire
   ```

   Ce fichier ne doit jamais être publié. Gardez une copie de `SECRET_KEY` en lieu sûr : la changer rend illisibles les clés API enregistrées.

4. **Adapter `docker-compose.yml`** à votre installation (adresse d'Ollama, ports, dossier des données), puis lancer :

   ```bash
   docker compose up -d --build
   ```

5. **Se connecter** avec le compte administrateur, puis vider `ADMIN_PASSWORD` dans `.env` une fois le compte créé.

## Sécurité

- L'accès se fait toujours par identifiant et mot de passe.
- N'exposez pas l'outil directement sur Internet : préférez un réseau privé ou un VPN, et HTTPS si possible.
- Le tableau de bord est servi séparément et réservé aux administrateurs.
- Tout document envoyé au tchat Claude quitte votre machine. La rédaction des prompts, elle, reste locale.
- Sauvegardez régulièrement le dossier des données et le fichier `.env`.

## Tests

```bash
pip install -r requirements.txt
python tests/run_tests.py
```

Les tests unitaires et d'intégration n'appellent ni Ollama, ni l'API Claude, ni la carte graphique. Ils tournent aussi automatiquement sur GitHub Actions à chaque envoi. Le mode d'emploi complet est dans `docs/tests.md`.

## Feuille de route

- Qualité des prompts : exemples de prompts idéaux dans le Modelfile.
- OCR pour les images et les PDF scannés.
- Prise en charge des anciens formats bureautiques et des formats libres (DOC, PPT, ODT, RTF).
- Conservation des conversations du tchat.
- Plus tard : affinage du modèle à partir des prompts idéaux.

Les idées et les retours sont les bienvenus dans les *issues*.

## Contribuer

Toute contribution est la bienvenue : correction, nouveau convertisseur, traduction, documentation, exemples de prompts.

1. Créez une branche à partir de `main`.
2. Lancez les tests avant d'envoyer vos modifications.
3. Signez vos commits avec `git commit -s`.
4. Ouvrez une *pull request* en expliquant ce que vous changez et pourquoi.

Les détails sont dans [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

Prompteur Claude est distribué sous licence [AGPL-3.0](LICENSE). Vous pouvez l'utiliser, le modifier et le partager librement. Toute version modifiée, distribuée ou mise en ligne doit publier son code sous la même licence.

Prompteur Claude est un projet indépendant, sans lien avec Anthropic.
