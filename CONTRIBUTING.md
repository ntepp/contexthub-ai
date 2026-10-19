# Contribuer à ContextHub AI

Merci de votre intérêt. ContextHub est publié sous [licence MIT](LICENSE) : en ouvrant une pull
request, vous acceptez que votre contribution soit distribuée sous cette même licence, et vous
confirmez en être l'auteur ou avoir le droit de la soumettre.

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
