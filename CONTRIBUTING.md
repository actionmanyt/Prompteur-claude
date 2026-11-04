# Contribuer au Prompteur Claude

## Licence

Ce projet est distribué sous **GNU AGPL-3.0**. Voir [`LICENSE`](LICENSE).

- **Gratuit** : aucune redevance.
- **Libre** : utilisation, étude, modification et redistribution autorisées.
- **Partage obligatoire** : toute version modifiée, distribuée **ou mise à disposition
  sur un réseau**, doit publier son code source complet sous AGPL-3.0.

## Proposer vos améliorations (fortement souhaité)

1. Faites un *fork* du dépôt.
2. Créez une branche : `git checkout -b ma-amelioration`.
3. Ouvrez une *Pull Request* vers `main` : ce que ça apporte, comment tester,
   impact sur le GPU, la mémoire ou les tokens.

Pas de Pull Request ? Ouvrez au moins une *Issue* avec un lien vers votre fork.

## Règles

- Jamais de secret dans Git : `.env`, clés API, `data/`, `*.db`.
- Rester compatible avec un GPU de 6 Go (`qwen3:4b-instruct`).
- Une Pull Request = un sujet.
- Mettre à jour `docs/modifier-prompteur.md` si le fonctionnement change.
- Signer ses commits (`git commit -s`), selon le Developer Certificate of Origin.