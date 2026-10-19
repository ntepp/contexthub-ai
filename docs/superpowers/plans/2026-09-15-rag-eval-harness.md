# RAG Evaluation Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mettre en place un harness d'évaluation reproductible qui exécute le golden dataset MiFID (18 questions), calcule les métriques de grounding/retrieval/génération, et produit des rapports JSON + CSV.

**Architecture:** Un CLI `eval/run_eval.py` charge le JSON golden, boucle de façon asynchrone sur `create_and_run_graph()`, délègue le scoring à `eval/harness/metrics.py` (refus exact, similarité sémantique via embeddings `bge-m3`, hit rate retrieval par basename PDF), et agrège via `eval/harness/reporter.py`. Les résultats sont écrits dans `eval/results/<timestamp>/`. Le golden v0.2 est commité en amont.

**Tech Stack:** Python 3.11, asyncio, `create_and_run_graph()` existant, `OllamaEmbeddings(bge-m3)`, stdlib `csv` + `json`, pas de nouvelle dépendance externe.

**Spec:** État actuel documenté dans la conversation du 2026-09-15 + `eval/datasets/golden_mifid.json` v0.2.

## Global Constraints

- `NO_ANSWER_TEXT` doit correspondre exactement à la valeur dans `src/rag/rag_pipeline.py` (refus = exact match, pas fuzzy).
- Les scores de retrieval utilisent les **distances** cosinus (pas similarité) — ne pas toucher au pipeline pour l'eval.
- Le harness appelle **`create_and_run_graph()`** tel quel (pas de mock LLM en run prod).
- Collection pgvector : configurable, défaut `"mifid"` (à ajouter dans metadata du golden).
- Pas de pytest obligatoire en CI pour l'instant — tests unitaires sur `metrics.py` et `reporter.py` seulement.
- Résultats gitignorés : `eval/results/` (artefacts générés).

---

## File Structure

```
eval/
├── datasets/
│   └── golden_mifid.json          # v0.2 — 18 questions (commit)
├── harness/
│   ├── __init__.py
│   ├── loader.py                  # charge + valide le JSON
│   ├── metrics.py                 # scoring unitaire + agrégation par catégorie
│   └── reporter.py                # export JSON + CSV
├── run_eval.py                    # CLI entry point
└── README.md                      # prérequis + commande + lecture des métriques

eval/results/                      # .gitignore — rapports horodatés
tests/eval/
├── test_metrics.py
└── test_reporter.py
```

**Modification pipeline (minimale) :** aucune — le runner consomme l'API publique `create_and_run_graph()`.

**Modification golden :** ajouter `"collection": "mifid"` dans `metadata`.

---

## Métriques — définitions exactes

### 1. Refusal exact match (`answerable: false`, 4 questions)

```python
def refusal_exact_match(predicted: str, no_answer: str) -> bool:
    return predicted.strip() == no_answer.strip()
```

- **Pass** si égalité stricte après strip.
- Métrique globale : `refusal_accuracy = correct_refusals / 4`.

### 2. Score sémantique (`answerable: true`, 14 questions)

Similarité cosinus entre embeddings `bge-m3` de `predicted_answer` et `ground_truth` :

```python
def semantic_score(predicted: str, ground_truth: str, embeddings) -> float:
    vec_a = embeddings.embed_query(predicted)
    vec_b = embeddings.embed_query(ground_truth)
    return cosine_similarity(vec_a, vec_b)  # float in [0, 1] approx
```

- **Pass** si `score >= EVAL_SEMANTIC_THRESHOLD` (défaut `0.75`, configurable via env ou CLI `--threshold`).
- Métrique globale : `semantic_mean`, `semantic_pass_rate`.

### 3. Hit rate retrieval par `source_document`

```python
def retrieval_hit(sources: list[dict], expected: str | None) -> bool:
    if expected is None:
        return True  # unanswerable — N/A
    expected_docs = [s.strip() for s in expected.split(";")]
    returned = [Path(s["source"]).name for s in sources]
    return all(any(exp in ret or ret.endswith(exp) for ret in returned) for exp in expected_docs)
```

- Pour `cross_document` (3 questions) : **tous** les PDF attendus doivent apparaître dans les sources retournées.
- Métrique globale : `retrieval_hit_rate = hits / 14`.

### 4. Métriques auxiliaires (déjà disponibles, à inclure dans le rapport)

- `confidence_metric` retourné par le pipeline
- `latency_seconds` par question
- `sources` brutes pour debug

### 5. Agrégation par catégorie

Pour chaque `category` (`factual_simple`, `definition`, `cross_document`, `reasoning_conditional`, `unanswerable_trap`) :

| Métrique | Applicable à |
|---|---|
| `count` | toutes |
| `refusal_accuracy` | `unanswerable_trap` |
| `semantic_mean` / `semantic_pass_rate` | answerable categories |
| `retrieval_hit_rate` | answerable categories |
| `latency_mean` | toutes |

---

## Rapport — format de sortie

### `eval/results/<timestamp>/summary.json`

```json
{
  "run_id": "2026-09-15T14-30-00",
  "dataset": "eval/datasets/golden_mifid.json",
  "dataset_version": "0.2",
  "collection": "mifid",
  "model": "llama3.1:8b",
  "thresholds": { "semantic": 0.75 },
  "global": {
    "total": 18,
    "refusal_accuracy": 1.0,
    "semantic_mean": 0.82,
    "semantic_pass_rate": 0.86,
    "retrieval_hit_rate": 0.79,
    "latency_mean_seconds": 3.1
  },
  "by_category": { "...": {} }
}
```

### `eval/results/<timestamp>/details.csv`

Colonnes : `id`, `category`, `answerable`, `question`, `predicted_answer`, `ground_truth`, `refusal_match`, `semantic_score`, `semantic_pass`, `retrieval_hit`, `confidence_metric`, `latency_seconds`, `sources_json`.

---

## Prérequis d'exécution (documentés dans eval/README.md)

1. `docker-compose up -d` (Postgres + Redis)
2. Ollama running + `bge-m3` + modèle LLM (`LLM_MODEL_NAME`)
3. Corpus MiFID indexé dans collection `mifid` (4 PDF EUR-Lex)
4. `.env` configuré (`NO_ANSWER_TEXT`, `RETRIEVAL_*`, etc.)

Commande :

```bash
python -m eval.run_eval \
  --dataset eval/datasets/golden_mifid.json \
  --collection mifid \
  --output eval/results \
  --threshold 0.75
```

---

### Task 1: Commit golden dataset v0.2

**Files:**
- Modify: `eval/datasets/golden_mifid.json`
- Modify: `.gitignore` (ajouter `eval/results/`)

**Interfaces:**
- Produces: golden v0.2 committed avec 18 questions + metadata enrichie

- [ ] **Step 1: Enrichir metadata**

Ajouter dans `metadata` :

```json
"collection": "mifid",
"expected_documents": [
  "RAG-CELEX_32014L0065_FR_TXT.pdf",
  "RAG-CELEX_32014R0600_FR_TXT.pdf",
  "RAG-CELEX_32012R0648_FR_TXT.pdf"
]
```

- [ ] **Step 2: Vérifier cohérence NO_ANSWER**

Confirmer que les 4 entrées `answerable: false` ont `ground_truth` identique à `NO_ANSWER_TEXT` du `.env`.

- [ ] **Step 3: Ajouter eval/results/ au .gitignore**

```
eval/results/
```

- [ ] **Step 4: Commit**

```bash
git add eval/datasets/golden_mifid.json .gitignore
git commit -m "eval: expand golden MiFID dataset to v0.2 (18 questions)"
```

---

### Task 2: Dataset loader + validation

**Files:**
- Create: `eval/__init__.py`
- Create: `eval/harness/__init__.py`
- Create: `eval/harness/loader.py`
- Test: `tests/eval/test_loader.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class GoldenQuestion:
      id: str
      category: str
      question: str
      ground_truth: str
      source_document: str | None
      reference_article: str | None
      answerable: bool

  @dataclass
  class GoldenDataset:
      metadata: dict
      questions: list[GoldenQuestion]

  def load_golden_dataset(path: str) -> GoldenDataset: ...
  def validate_dataset(ds: GoldenDataset, no_answer: str) -> list[str]: ...
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_loader.py
from eval.harness.loader import load_golden_dataset, validate_dataset

def test_load_golden_has_18_questions():
    ds = load_golden_dataset("eval/datasets/golden_mifid.json")
    assert len(ds.questions) == 18
    assert ds.metadata["version"] == "0.2"

def test_validate_refusal_ground_truth_matches_no_answer():
    ds = load_golden_dataset("eval/datasets/golden_mifid.json")
    no_answer = "L'information n'est pas disponible dans la base de connaissances actuelle."
    errors = validate_dataset(ds, no_answer)
    assert errors == []
```

- [ ] **Step 2: Run test — expected FAIL**

Run: `python -m pytest tests/eval/test_loader.py -v`

- [ ] **Step 3: Implement loader.py**

Charger JSON, parser en dataclasses, valider :
- IDs uniques
- Champs requis présents
- `answerable: false` → `ground_truth == no_answer` et `source_document is None`
- `answerable: true` → `source_document` non null

- [ ] **Step 4: Run test — expected PASS**

- [ ] **Step 5: Commit**

```bash
git add eval/harness/loader.py tests/eval/test_loader.py eval/__init__.py eval/harness/__init__.py
git commit -m "eval: add golden dataset loader with validation"
```

---

### Task 3: Metrics module

**Files:**
- Create: `eval/harness/metrics.py`
- Test: `tests/eval/test_metrics.py`

**Interfaces:**
- Consumes: `GoldenQuestion`, réponse pipeline `{answer, sources, confidence_metric, latency_seconds}`
- Produces:
  ```python
  @dataclass
  class QuestionResult:
      id: str
      category: str
      answerable: bool
      question: str
      predicted_answer: str
      ground_truth: str
      refusal_match: bool | None      # None si answerable
      semantic_score: float | None    # None si not answerable
      semantic_pass: bool | None
      retrieval_hit: bool | None
      confidence_metric: float
      latency_seconds: float
      sources: list[dict]

  def score_question(q: GoldenQuestion, response: dict, *, no_answer: str, embeddings, threshold: float) -> QuestionResult: ...
  def aggregate_results(results: list[QuestionResult]) -> dict: ...
  ```

- [ ] **Step 1: Write failing tests**

```python
def test_refusal_exact_match():
    from eval.harness.metrics import refusal_exact_match
    no_answer = "L'information n'est pas disponible dans la base de connaissances actuelle."
    assert refusal_exact_match(no_answer, no_answer) is True
    assert refusal_exact_match(no_answer + " ", no_answer) is True
    assert refusal_exact_match("Je ne sais pas.", no_answer) is False

def test_retrieval_hit_single_doc():
    from eval.harness.metrics import retrieval_hit
    sources = [{"source": "uploads/abc_RAG-CELEX_32014L0065_FR_TXT.pdf", "page": 12}]
    assert retrieval_hit(sources, "RAG-CELEX_32014L0065_FR_TXT.pdf") is True

def test_retrieval_hit_cross_doc_requires_all():
    from eval.harness.metrics import retrieval_hit
    sources = [{"source": "uploads/RAG-CELEX_32012R0648_FR_TXT.pdf", "page": 1}]
    expected = "RAG-CELEX_32012R0648_FR_TXT.pdf; RAG-CELEX_32014R0600_FR_TXT.pdf"
    assert retrieval_hit(sources, expected) is False

def test_aggregate_by_category():
    # mock QuestionResult list, assert by_category keys and counts
    ...
```

- [ ] **Step 2: Run tests — expected FAIL**

- [ ] **Step 3: Implement metrics.py**

Inclure `cosine_similarity` (numpy ou pure Python — préférer pure Python pour éviter nouvelle dep).

- [ ] **Step 4: Run tests — expected PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "eval: add scoring metrics (refusal, semantic, retrieval hit rate)"
```

---

### Task 4: Reporter module

**Files:**
- Create: `eval/harness/reporter.py`
- Test: `tests/eval/test_reporter.py`

**Interfaces:**
- Consumes: `list[QuestionResult]`, `aggregate_results()` output, run metadata
- Produces:
  ```python
  def write_report(results: list[QuestionResult], summary: dict, output_dir: Path) -> Path: ...
  # writes summary.json + details.csv, returns output_dir
  ```

- [ ] **Step 1: Write failing test**

Vérifier qu'après `write_report()`, `summary.json` et `details.csv` existent avec les bonnes colonnes CSV.

- [ ] **Step 2: Run test — expected FAIL**

- [ ] **Step 3: Implement reporter.py**

- [ ] **Step 4: Run test — expected PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "eval: add JSON/CSV report writer"
```

---

### Task 5: Async runner CLI

**Files:**
- Create: `eval/run_eval.py`
- Modify: `requirements.txt` (ajouter `pytest` en dev — optionnel, commenté)

**Interfaces:**
- Consumes: loader, metrics, reporter, `create_and_run_graph`
- Produces: CLI exécutable via `python -m eval.run_eval`

- [ ] **Step 1: Implement run_eval.py skeleton**

```python
async def run_evaluation(
    dataset_path: str,
    collection: str,
    output_dir: str,
    threshold: float,
) -> Path:
    ds = load_golden_dataset(dataset_path)
    no_answer = os.getenv("NO_ANSWER_TEXT", "...")
    errors = validate_dataset(ds, no_answer)
    if errors:
        raise SystemExit("\n".join(errors))

    embeddings = initialize_embeddings()
    results = []
    for q in ds.questions:
        print(f"[eval] {q.id} — {q.question[:60]}...")
        response = await create_and_run_graph(q.question, collection=collection)
        results.append(score_question(q, response, no_answer=no_answer, embeddings=embeddings, threshold=threshold))

    summary = aggregate_results(results)
    summary["run_id"] = datetime.now().isoformat()
    summary["collection"] = collection
    summary["model"] = os.getenv("LLM_MODEL_NAME")
    summary["thresholds"] = {"semantic": threshold}

    out = Path(output_dir) / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    write_report(results, summary, out)
    _print_summary(summary)
    return out
```

- [ ] **Step 2: Add argparse CLI**

Flags : `--dataset`, `--collection`, `--output`, `--threshold`, `--ids` (filtre Q001,Q002 pour debug).

- [ ] **Step 3: Manual smoke test**

Run: `python -m eval.run_eval --ids Q005` (1 question unanswerable, rapide)

Expected: rapport généré, `refusal_match` calculé.

- [ ] **Step 4: Full run**

Run: `python -m eval.run_eval` sur les 18 questions.

Expected: `eval/results/<timestamp>/summary.json` + `details.csv`.

- [ ] **Step 5: Commit**

```bash
git commit -m "eval: add async evaluation runner CLI"
```

---

### Task 6: Documentation + pytest setup

**Files:**
- Create: `eval/README.md`
- Create: `tests/__init__.py`, `tests/eval/__init__.py`
- Modify: `requirements.txt` (ajouter `pytest`)
- Modify: `CLAUDE.md` (section commande eval)

**Interfaces:**
- Produces: doc opérationnelle + commande de test unitaire

- [ ] **Step 1: Write eval/README.md**

Contenu :
- Prérequis infra
- Indexation corpus MiFID (commande `/load` ou upload)
- Commande full eval
- Interprétation des métriques + seuils recommandés
- Structure du rapport

- [ ] **Step 2: Add pytest to requirements.txt**

```
pytest
```

- [ ] **Step 3: Run unit tests**

Run: `python -m pytest tests/eval/ -v`

Expected: all PASS (metrics + reporter + loader, no LLM)

- [ ] **Step 4: Update CLAUDE.md**

Ajouter :

```bash
python -m eval.run_eval --collection mifid
python -m pytest tests/eval/ -v
```

- [ ] **Step 5: Commit**

```bash
git commit -m "docs: add eval harness README and pytest setup"
```

---

## Ordre d'exécution recommandé

```
Task 1 (golden commit)
  → Task 2 (loader)
  → Task 3 (metrics)
  → Task 4 (reporter)
  → Task 5 (runner CLI)
  → Task 6 (docs + pytest)
  → Full eval run + review résultats
```

## Critères de succès

| Critère | Cible initiale (baseline) |
|---|---|
| Harness exécutable en 1 commande | ✅ |
| 18/18 questions exécutées sans crash | ✅ |
| Rapport JSON + CSV générés | ✅ |
| `refusal_accuracy` mesuré sur 4 pièges | ✅ |
| `semantic_mean` + pass rate sur 14 answerable | ✅ |
| `retrieval_hit_rate` sur 14 answerable | ✅ |
| Agrégation par catégorie dans summary | ✅ |
| Tests unitaires metrics/reporter/loader passent sans LLM | ✅ |

> Les **seuils de qualité** (ex. refusal_accuracy = 100%, semantic_pass_rate > 80%) seront fixés **après la première baseline run**, pas avant — l'objectif de ce plan est l'instrumentation, pas l'optimisation du pipeline.

## Risques & mitigations

| Risque | Mitigation |
|---|---|
| Corpus non indexé → tous refus | Pre-flight : vérifier collection non vide via `/documents?collection=mifid` |
| Run long (~18 × 3-5s LLM) | Flag `--ids` pour debug ; log progression |
| Score sémantique instable | Seuil configurable ; inclure scores bruts dans CSV pour analyse |
| cross_document miss retrieval (TOP_K=4) | Reporter inclut `sources` brutes ; documenter comme known limitation |
| Ollama down | Catch exception par question, marquer `error` dans CSV, continuer le run |

## Extensions futures (hors scope)

- LLM-as-judge (GPT-4o) pour scoring plus robuste
- CI GitHub Actions avec eval sur PR (coût LLM)
- Comparaison de runs (diff entre deux `summary.json`)
- Métrique faithfulness article-level (match `reference_article` dans contexte)
