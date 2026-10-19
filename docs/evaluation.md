# Évaluation de la qualité

ContextHub est livré avec un banc d'évaluation reproductible. Il sert à trois choses :
**prouver** la qualité avant une vente, **valider** une installation sur le corpus d'un client,
et **empêcher les régressions** à chaque modification du pipeline.

## Jeu de référence

Le banc ne dépend d'aucun métier : un jeu de référence est un fichier JSON de questions et de
réponses attendues, à constituer sur n'importe quel corpus (RH, IT, contrats…). Le jeu livré
sert de démonstration, sur un corpus public et exigeant.

`eval/datasets/golden_mifid.json` (v0.2) : 18 questions sur trois textes EUR-Lex en français —
MiFID II (directive 2014/65/UE), MiFIR (règlement 600/2014), EMIR (règlement 648/2012), soit
272 pages. Réponses de référence vérifiées ligne à ligne dans les PDF.

| Catégorie | Questions | Ce qui est testé |
|---|---|---|
| `factual_simple` | 4 | Une date, un seuil, une obligation précise |
| `definition` | 4 | Une définition légale (article 4 MiFID II, article 2 EMIR…) |
| `cross_document` | 3 | Une réponse qui combine deux textes |
| `reasoning_conditional` | 3 | Une condition ou une exception |
| `unanswerable_trap` | 4 | Une question plausible dont la réponse **n'est pas** dans le corpus : seul le refus est correct |

## Métriques

| Métrique | Définition |
|---|---|
| Refus corrects | Part des pièges où la réponse est exactement `NO_ANSWER_TEXT` |
| Refus à tort | Part des questions répondables où le système a refusé |
| Score sémantique | Similarité cosinus (bge-m3) entre la réponse et la référence ; réussite si ≥ 0,75 |
| Bonne source | Tous les documents attendus figurent parmi les sources retournées |
| Bon article | L'article attendu figure dans la section d'une source **citée** |
| Calibration | Pertinence moyenne des sources pour les réponses justes vs fausses |

## Lancer une évaluation

```bash
# Indexer le corpus puis évaluer (collection créée si besoin)
python -m eval.run_eval --collection mifid --ingest ./corpus/mifid
# Rejouer sur un autre modèle autorisé
python -m eval.run_eval --collection mifid --model anthropic:claude-sonnet-5-5
# En production
docker compose exec api entrypoint.sh eval --collection mifid
```

Chaque exécution écrit `eval/results/<horodatage>/summary.json` (agrégats, paramètres de
recherche, modèle) et `details.csv` (une ligne par question, sources incluses).

Pour un client : constituez 20 à 50 questions sur son corpus avec ses experts (même format JSON),
dont au moins 20 % de pièges, et rejouez-les à chaque changement de modèle ou de paramètre.

## Résultats (2026-10-14)

Configuration : 100 % local, `qwen3.5:9b` via Ollama (réflexion désactivée), embeddings
`bge-m3`, recherche hybride, `RETRIEVAL_TOP_K=8`, `RETRIEVAL_CANDIDATES=40`,
`RETRIEVAL_MAX_DISTANCE=0.75`. Matériel : portable, GPU RTX 3050 4 Go (le modèle tourne à 73 %
sur CPU).

| | Version initiale | v1 | Cible PRD |
|---|---|---|---|
| Réussite globale | 72 % | **89 %** | — |
| Refus corrects sur pièges | 100 % | **100 %** | 100 % |
| Refus à tort | 29 % | **7 %** | — |
| Bonne source récupérée | 57 % | **86 %** | ≥ 85 % |
| Bon article cité | 50 % | **90 %** | — |
| Score sémantique moyen | 0,59 | **0,77** | ≥ 0,80 |
| Latence moyenne | 43 s | 69 s* | < 5 s au premier jeton sur GPU 8 Go |

\* Latence de bout en bout sur un GPU de 4 Go partagé avec d'autres tâches pendant la mesure ;
elle n'est pas représentative d'un serveur. À mesurer sur le matériel cible (GPU 16 Go) avant
d'annoncer un chiffre.

Par catégorie (v1) : `factual_simple` 100 %, `reasoning_conditional` 100 %,
`unanswerable_trap` 100 %, `definition` 75 %, `cross_document` 67 %.

### Ce qui a fait passer de 72 % à 89 %

L'analyse question par question de la première mesure a montré que les refus à tort ne venaient
pas du seuil de distance — tous les candidats étaient sous 0,75 — mais du fait que le **passage
attendu n'atteignait pas le contexte** du LLM (rang 21 à 52 en recherche vectorielle) :

1. **Noms d'usage des textes.** Le texte d'EMIR ne contient jamais le mot « EMIR » : il se
   désigne comme « le présent règlement ». Les actes européens sont maintenant reconnus sur leur
   première page et leur nom d'usage (MiFID II, EMIR, DORA, SFDR…) est ajouté en tête de chaque
   passage (`src/ingestion/eu_acts.py`).
2. **Accents.** « financiere » ne rejoignait pas « financière » dans l'index plein texte :
   configuration `fr_unaccent` (unaccent + racinisation française).
3. **Racines.** « définit » (`defin`) ne rejoignait pas « Définitions » (`definit`) : recherche
   par préfixe sur les mots longs.
4. **Pool de candidats** porté de 20 à 40, contexte de 6 à 8 passages.

### Limites connues

- **Longues listes de définitions** (Q010) : l'article 2 d'EMIR compte une trentaine de
  définitions ; le passage contenant le point 8 n'entre pas dans les 8 retenus. Pistes : découper
  les articles de définitions point par point, activer le re-classement (`RERANKER=cross_encoder`).
- **Questions sur deux textes** (Q003) : un seul des deux textes est retrouvé. Piste :
  décomposer la question en sous-questions, une recherche par texte.
- **Calibration** : la pertinence des sources (`confidence_metric`) ne sépare pas les réponses
  justes des fausses (0,665 contre 0,669). Elle n'est donc **pas affichée** dans l'interface ;
  elle reste dans l'API et le journal d'audit pour analyse.
- 18 questions ne suffisent pas à une garantie statistique : une seule question pèse plus de
  5 points de pourcentage. Le jeu doit être étendu (objectif : 100 questions).
