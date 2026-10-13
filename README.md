# ContextHub AI

**Assistant documentaire souverain pour les équipes conformité.** ContextHub répond aux questions
de vos collaborateurs à partir de vos textes de référence — réglementation, doctrine, procédures
internes — en citant chaque affirmation à la page et à l'article près. Quand vos documents ne
permettent pas de répondre, il le dit. Chaque échange est journalisé dans un registre d'audit
infalsifiable.

Il s'installe chez vous. En mode local, aucun contenu ne quitte votre infrastructure.

![Licence AGPL-3.0](https://img.shields.io/badge/licence-AGPL--3.0%20%2B%20commerciale-1E5B48)
![Python 3.12](https://img.shields.io/badge/python-3.12-16213A)
![PostgreSQL + pgvector](https://img.shields.io/badge/PostgreSQL-pgvector-16213A)

---

## Ce que fait ContextHub

| | |
|---|---|
| **Réponses citées** | Chaque phrase renvoie à une source numérotée `[n]` : document, page, chemin Titre › Chapitre › Article, extrait. Un clic ouvre le PDF à la bonne page. |
| **Refus explicite** | Si aucun passage pertinent n'existe, réponse standard de refus, sans même appeler le LLM. Testé : 100 % de refus corrects sur les questions pièges du jeu de référence. |
| **Textes européens reconnus** | MiFID II, MiFIR, EMIR, DORA, SFDR, MAR, PRIIPs, AIFM, CRR… identifiés automatiquement : une question sur « EMIR » trouve le règlement 648/2012. |
| **Recherche hybride** | Sémantique (bge-m3, multilingue) et plein texte français sans accents, fusionnées ; découpage qui respecte les articles. |
| **Journal d'audit** | Questions, réponses, sources, consultations, imports, droits : tout est tracé, chaîné par SHA-256, vérifiable et exportable en CSV. |
| **Accès maîtrisés** | SSO OpenID Connect (Entra ID, Google, Okta, Keycloak) ou comptes locaux, droits par collection, clés d'API. |
| **RGPD** | Suppression de documents, de collections et d'utilisateurs ; durées de conservation ; effacement des données personnelles du journal sans casser son intégrité. |
| **Fournisseur de LLM au choix** | Ollama en local par défaut ; Anthropic, OpenAI, Mistral ou tout endpoint compatible OpenAI si vous l'autorisez. L'interface signale en permanence quand un modèle cloud est utilisé. |
| **Qualité mesurée** | Banc d'évaluation livré avec un jeu de référence MiFID / MiFIR / EMIR ; à rejouer sur votre propre corpus. |

## Architecture

```
 Navigateur ──▶ nginx ──▶ API FastAPI ──▶ PostgreSQL + pgvector  (comptes, droits, audit, passages indexés)
                 │           │      └──▶ Redis                   (file d'attente, limites de débit)
                 │           │      └──▶ LangGraph : reformulation → recherche hybride → génération citée
                 │           └──▶ Worker Celery : PDF/DOCX/HTML/TXT → OCR → découpage par article → embeddings
                 └ seul service exposé            └──▶ Ollama (local)  ou  fournisseur cloud autorisé
```

| Module | Rôle |
|---|---|
| `src/api/` | Routes, authentification, contrôle d'accès par collection |
| `src/auth/` | Mots de passe, sessions, OIDC, clés d'API, limitation de débit |
| `src/audit/` | Journal chaîné, vérification, export, effacement RGPD |
| `src/ingestion/` | Formats, OCR, reconnaissance des actes européens, découpage structuré, import d'URL sécurisé |
| `src/retrieval/` | Stockage pgvector, recherche hybride RRF, re-classement optionnel |
| `src/rag/` | Graphe LangGraph `condense → retrieve → generate`, citations, refus |
| `src/llm/` | Fournisseurs et liste blanche des modèles |
| `eval/` | Banc d'évaluation et jeu de référence |
| `frontend/` | Interface React (français) |

## Démarrage

### Production (chez un client)

```bash
cp .env.production.example .env        # remplacer chaque « change-me »
docker compose -f docker-compose.yaml -f docker-compose.gpu.yaml --profile ollama up -d --build
docker compose exec ollama ollama pull bge-m3 && docker compose exec ollama ollama pull llama3.1:8b
```

Guide complet : [docs/deployment.md](docs/deployment.md) · sécurité et flux de données :
[docs/security.md](docs/security.md).

### Développement

```bash
python -m venv venv && venv\Scripts\activate          # source venv/bin/activate sous Linux/macOS
pip install -r requirements-dev.txt
cd frontend && npm ci && cd ..
cp .env.example .env
ollama pull bge-m3 && ollama pull llama3.1:8b
start.bat                                              # ou ./start.sh : PostgreSQL, Redis, migrations, API, worker, interface
```

Interface : http://localhost:5173 (compte défini par `ADMIN_EMAIL` / `ADMIN_PASSWORD`) ·
API : http://localhost:8000/docs.

## Tests et qualité

```bash
ruff check src eval tests
pytest                                                 # SQLite, sans service externe
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/contexthub_test pytest   # + PostgreSQL réel
cd frontend && npm run build
python -m eval.run_eval --collection mifid             # banc d'évaluation (LLM et corpus réels)
```

Résultats d'évaluation et méthode : [docs/evaluation.md](docs/evaluation.md).

## API

Toutes les routes métier exigent une session (cookie) ou une clé d'API
(`Authorization: Bearer chk_…`).

| Méthode | Route | |
|---|---|---|
| `POST` | `/auth/login`, `/auth/logout` | Connexion par mot de passe |
| `GET` | `/auth/oidc/login` | Connexion SSO |
| `GET` | `/collections` | Collections accessibles |
| `POST` | `/collections/{id}/documents` | Importer un document (asynchrone) |
| `DELETE` | `/documents/{id}` | Supprimer un document |
| `GET` | `/documents/{id}/file` | Fichier original |
| `POST` | `/chat/ask` | Question → réponse, sources, conversation |
| `POST` | `/chat/stream` | Idem en streaming (Server-Sent Events) |
| `GET` | `/conversations` | Historique |
| `GET` | `/admin/audit`, `/admin/audit/export.csv`, `/admin/audit/verify` | Journal d'audit |
| `GET` | `/health`, `/ready`, `/metrics` | Supervision |

Exemple :

```bash
curl -X POST https://contexthub.client.fr/api/chat/ask \
  -H "Authorization: Bearer chk_..." -H "Content-Type: application/json" \
  -d '{"question": "Quand la directive MiFID II est-elle applicable ?"}'
```

```json
{
  "answer": "Les États membres appliquent ces dispositions à partir du 3 janvier 2017 [1].",
  "sources": [{"index": 1, "filename": "CELEX_32014L0065_FR.pdf", "page": 131,
               "section_path": ["MiFID II — Directive 2014/65/UE", "Titre VII", "Article 93 — Transposition"],
               "cited": true}],
  "refused": false,
  "conversation_id": "…"
}
```

## Documentation

- [PRD — produit, cible, périmètre, suivi](docs/PRD.md)
- [Installation et exploitation](docs/deployment.md)
- [Sécurité et flux de données](docs/security.md)
- [Évaluation de la qualité](docs/evaluation.md)
- [Incidents et retours d'expérience](docs/incidents.md)

## Licence

Double licence : [AGPL-3.0](LICENSE) pour l'édition communautaire, licence commerciale pour les
clients qui ne souhaitent pas en appliquer les obligations et veulent le support éditeur. Voir
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md). Les versions antérieures à la v1.0 restent sous
licence MIT.
