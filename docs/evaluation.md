# Évaluation de la qualité

ContextHub est livré avec un banc d'évaluation reproductible. Il sert à trois choses :
**prouver** la qualité avant une vente, **valider** une installation sur le corpus d'un client,
et **empêcher les régressions** à chaque modification du pipeline.

## Jeux de référence

Le banc ne dépend d'aucun métier : un jeu de référence est un fichier JSON de questions et de
réponses attendues, à constituer sur n'importe quel corpus (RH, IT, contrats…). Deux jeux sont
livrés sur des corpus publics de nature très différente, un troisième sur des manuels techniques
propriétaires que l'utilisateur fournit :

| Jeu | Corpus | Format | Questions |
|---|---|---|---|
| `golden_rh.json` | 9 fiches Service-Public.fr sur le droit du travail (instantané livré dans `eval/corpora/rh`) | Markdown structuré par titres | 20 dont 5 pièges |
| `golden_mifid.json` | MiFID II, MiFIR, EMIR (EUR-Lex, à télécharger) | PDF de 272 pages, articles | 18 dont 4 pièges |
| `golden_manuals.json` | 3 manuels d'onduleurs Nitram (CyberPower), **non versionnés** | PDF : 80 pages lues par OCR, 38 + 2 pages natives ; sections « 3.2.2 », tableaux | 20 dont 5 pièges (3 croisés entre modèles) |

### Jeu RH (démonstration hors finance)

`eval/datasets/golden_rh.json` (v1.0) : 20 questions de salariés et de gestionnaires RH sur
9 fiches pratiques de Service-Public.fr — période d'essai, démission, congés payés, congé pour
décès, rupture conventionnelle, jours fériés, télétravail, CDI, modification du contrat. Deux
fiches (CDI, modification du contrat) ne portent aucune question : elles servent de distracteurs.

| Catégorie | Questions | Exemple |
|---|---|---|
| `factual_simple` | 8 | « Quelle est la durée maximale de la période d'essai initiale d'un cadre en CDI ? » |
| `reasoning_conditional` | 5 | « Quel préavis pour un salarié payé au mois qui démissionne en Moselle ? » |
| `cross_document` | 2 | Période d'essai et rupture conventionnelle ; télétravail et journée de solidarité |
| `unanswerable_trap` | 5 | Congé pour mariage, SMIC 2026, congé de paternité… : absents des fiches, seul le refus est correct |

Le corpus est un **instantané** (22 octobre 2026), livré dans le dépôt pour que les mesures
soient reproductibles : les fiches sont publiées par la DILA sous licence Etalab 2.0 (Licence
Ouverte), qui en autorise la copie avec mention de la source, portée en tête de chaque fichier.
Pour le rafraîchir, `python -m eval.tools.fetch_service_public eval/corpora/rh F2278 F1643 …`,
puis revérifiez les réponses attendues (les fiches suivent l'évolution du droit).

Ici les passages ne portent pas de numéro d'article : `reference_article` contient un titre de
section (« Décès d'un proche ») et la métrique « bon article » vérifie qu'il figure dans le
chemin de section d'une source citée.

### Jeu MiFID (réglementation financière)

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

### Jeu manuels techniques (recette Nitram)

`eval/datasets/golden_manuals.json` (v1.0) reprend l'échec de recette du 2026-11 : à la question
« j'ai une alarme sur mon onduleur Nitram US30010CNG qui bipe. comment eteindre le bip? »,
ContextHub répondait avec la procédure du modèle US10000TSARM. Le corpus : le manuel
US30010/15/20/30/40CNG (80 pages dont le texte est en images, lu par OCR), le manuel de la série
US10000TSARM (38 pages, texte natif) et sa fiche produit (2 pages).

| Catégorie | Questions | Ce qui est testé |
|---|---|---|
| `procedure_multipage` | 5 | Une procédure sur plusieurs pages ou tableaux (MAN01 : pages 42-44 du PDF) |
| `factual_simple` / `reasoning_conditional` | 10 | Une valeur, un voyant, un code d'événement, pour un modèle cité |
| `trap_other_model` | 3 | MAN16 cite un modèle absent (US30050CNG : la notation US30010/15/20/30/40CNG ne le contient pas) — refus **avec** la mention des modèles trouvés ; MAN17 et MAN18 demandent pour un TSARM ce que seul le manuel CNG décrit |
| `unanswerable_trap` | 2 | Prix, durée de garantie : dans aucun document |

Champs propres à ce jeu : `expected_pages` (pages du **fichier PDF**, 1 = première page ; MAN01 :
42-44, imprimées 32-34), `product_documents` (documents qui concernent le produit cité, pour
mesurer la part de passages d'un autre produit), `expected_notice_refs` (référence que la mention
« autre produit » doit nommer). Les documents appartiennent au fabricant : ni les PDF ni leur
texte ne sont versionnés, le jeu ne cite que des pages. Pour le rejouer, copiez les trois PDF
dans `eval/corpora/manuals/` (ignoré par git) sous les noms de `metadata.corpus_files` ; l'outil
de mesure vérifie leurs empreintes SHA-256 (même édition que celle qui a servi à écrire le jeu).

## Mesure de la récupération seule

`python -m eval.tools.measure_retrieval` fait passer chaque question par le nœud `retrieve` du
pipeline — recherche, seuil, références produit, garde-fou, passages voisins — sans
reformulation, sans planificateur, sans LLM : ce qui est mesuré est ce que le modèle aurait à
lire. Utile quand aucun LLM n'est disponible, et pour isoler un problème de récupération d'un
problème de génération.

```bash
# Corpus indexé en mémoire (base configurée intacte) avec le modèle d'embedding configuré et l'OCR
python -m eval.tools.measure_retrieval --dataset eval/datasets/golden_manuals.json --corpus eval/corpora/manuals
# Collection déjà indexée de la base configurée (PostgreSQL), avec le diagnostic d'une question
python -m eval.tools.measure_retrieval --dataset eval/datasets/golden_manuals.json --collection "Manuel utilisateur" --explain MAN01
```

Par question : document attendu parmi les passages (`doc_hit`), page attendue parmi ceux de ce
document (`page_hit` : page de début, celle de la citation ; `page_hit_span` : toute page couverte
par le passage), rang du premier bon passage parmi les passages retenus (voisins exclus, `mrr`),
part de passages d'un autre produit, taille du contexte, issue (`refused` sans LLM, `notice` :
refus avec mention du produit, `llm`). Rapport JSON dans `eval/results/retrieval-<horodatage>.json`.

Pour diagnostiquer une question sur la vraie base : `python -m src.cli explain-retrieval
--collection "Manuel utilisateur" "la question"` (candidats, distance, rangs vecteur et plein
texte, document, page, section, rapport à la référence citée, rang final, voisins, raison
d'exclusion ; `--json`, `--excerpt 200`).

## Métriques

| Métrique | Définition |
|---|---|
| Refus corrects | Part des pièges où la réponse est exactement `NO_ANSWER_TEXT` (ou `NO_ANSWER_TEXT` suivi de la mention « autre produit », refus sans sources) |
| Refus à tort | Part des questions répondables où le système a refusé |
| Score sémantique | Similarité cosinus (bge-m3) entre la réponse et la référence ; réussite si ≥ 0,75 |
| Bonne source | Tous les documents attendus figurent parmi les sources retournées |
| Bon article | L'article attendu (« Article 2 ») ou, sans numéro d'article, le titre de section attendu figure dans le chemin de section d'une source **citée** |
| Calibration | Pertinence moyenne des sources pour les réponses justes vs fausses |

## Lancer une évaluation

```bash
# Jeu RH : le corpus livré (metadata.corpus_dir) est indexé automatiquement
python -m eval.run_eval --dataset eval/datasets/golden_rh.json
# Jeu MiFID : indexer les PDF téléchargés puis évaluer (collection créée si besoin)
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

## Résultats — manuels techniques, récupération seule (2026-11-22)

Mesure de `eval.tools.measure_retrieval` sur `golden_manuals.json`, avant (`master` 86d7aaa) et
après la branche `feature/technical-docs-reliability`, **dans la VM de développement et non sur
l'installation du client** :

- embeddings `intfloat/multilingual-e5-small` (ONNX quantifié, hors ligne), **pas `bge-m3`** :
  les distances sont plus resserrées (0,12 à 0,25) et le seuil de 0,75 laisse tout passer, la
  mesure porte donc sur le **classement**, pas sur le refus par le seuil ;
- partie plein texte : l'approximation en mémoire (`InMemoryChunkStore`, mots exacts), **pas**
  la recherche PostgreSQL (`fr_unaccent`, racines, préfixes) ;
- OCR Tesseract 4.1 **en anglais seulement** (`fra` absent de la VM) : accents perdus, confiance
  moyenne 85,8 (86 en recette, avec `fra+eng`) ;
- aucun LLM : les pièges MAN17 à MAN20 dépendent du refus du modèle et ne sont pas mesurés ici.

| Indicateur (15 questions répondables, 20 au total) | Avant | Après |
|---|---|---|
| Bon document parmi les passages | 93 % (14/15) | **100 %** |
| Bonne page parmi les passages (page de début) | 67 % (10/15) | **87 %** (13/15) |
| Rang réciproque moyen du premier bon passage | 0,31 | **0,53** |
| Part de passages d'un autre produit (20 questions) | 34 % | **0 %** |
| MAN01 (incident) | 0 passage du manuel CNG sur 8 (8 TSARM) | pages 42-43 (icône « Bouton Mute ») et 43-44 (« désactiver le buzzer ») dans le contexte |
| MAN16 (modèle absent) | 8 passages d'autres modèles transmis au LLM | refus sans LLM : « … Les sources trouvées concernent US10001TSARM, US10001.5TSARM, US30010CNG, US30015CNG, US10000TSARM, pas US30050CNG. » |
| Pages du manuel CNG lues par OCR | 74 | 76 (pages 66 et 68 : couche texte inexploitable) |
| Passages du manuel CNG | 88 | 168 (sections numérotées, 800 caractères) |
| Taille moyenne du contexte | 9 600 caractères | 10 800 caractères (voisins inclus, budget 12 000) |

Restent manqués : MAN12 (code d'événement 07, page 27 : aucun passage des pages 26-27 parmi les 8 retenus) et MAN14
(poids du US10003TSARM : la fiche produit, qui donne aussi 26,7 kg, passe devant le manuel). Pour
MAN01, le tableau 3.3 des touches (section 3.2.2, page 42) n'entre pas dans le contexte : il est
dans la section voisine de celle de l'icône. **À refaire sur l'installation du client** avec
`bge-m3`, PostgreSQL et l'OCR `fra+eng`, puis de bout en bout avec le LLM (`eval.run_eval`).
