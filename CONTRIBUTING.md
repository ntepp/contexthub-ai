# Contribuer à ContextHub AI

Merci de votre intérêt. ContextHub est publié sous double licence (AGPL-3.0 et commerciale,
voir [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)).

## Contrat de licence de contributeur (CLA)

Avant qu'une contribution puisse être fusionnée, son auteur accepte, en l'indiquant dans la
description de la pull request (« J'accepte le CLA de ContextHub ») :

1. qu'il est l'auteur de la contribution, ou qu'il a le droit de la soumettre ;
2. qu'il concède au mainteneur du projet une licence mondiale, non exclusive, gratuite,
   irrévocable, d'utiliser, modifier, sous-licencier et distribuer la contribution, **y compris
   sous une licence commerciale** ;
3. que la contribution reste par ailleurs disponible sous AGPL-3.0.

Sans ce contrat, une contribution ne pourrait être redistribuée que sous AGPL, ce qui rendrait
la licence commerciale impossible.

## Développement

```bash
python -m venv venv && venv\Scripts\activate      # Windows (source venv/bin/activate ailleurs)
pip install -r requirements-dev.txt
cd frontend && npm ci && cd ..
cp .env.example .env
start.bat                                          # ou ./start.sh
```

## Avant d'ouvrir une pull request

```bash
ruff check src eval tests
pytest                                             # SQLite, sans service externe
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/contexthub_test pytest -m pg
cd frontend && npm run build
```

Toute modification du pipeline de récupération ou des prompts doit être accompagnée d'un
passage du banc d'évaluation (`python -m eval.run_eval`) et des chiffres obtenus dans la
description de la pull request.

## Signaler une vulnérabilité

N'ouvrez pas d'issue publique : écrivez à ntepp.marcus@gmail.com. Voir [docs/security.md](docs/security.md).
