# PRD — ContextHub AI v1

| | |
|---|---|
| **Statut** | v1.0 livrée sur `master` (PR #2, 2026-10-19) ; évolutions livrées fonctionnalité par fonctionnalité (§13) |
| **Auteur** | ntepp |
| **Dernière mise à jour** | 2026-11-22 |
| **Licence produit** | MIT (open source) ; revenus par les services et le support |

---

## 1. Résumé

ContextHub AI est un **assistant documentaire souverain et générique** pour les entreprises et
les organisations, quel que soit leur secteur. Il répond aux questions des collaborateurs
**uniquement à partir des documents de l'entreprise** (procédures, politiques internes, contrats,
documentation technique, réglementation), cite systématiquement ses sources (document, page,
section), **refuse de répondre** quand le corpus ne contient pas l'information, et **journalise
chaque échange** pour l'audit.

Il s'installe **chez le client** (on-premise, une instance par client). Le LLM peut être 100 % local
(Ollama) ou, si le client le choisit, un fournisseur cloud (Anthropic, OpenAI, Mistral ou tout
endpoint compatible OpenAI).

## 2. Problème

- Les collaborateurs passent un temps considérable à retrouver la bonne information dans une
  documentation volumineuse et dispersée : RH (convention collective, accords), IT (procédures,
  base de connaissances), juridique (contrats), qualité (normes, modes opératoires), commercial
  (offres, fiches produits), conformité (réglementation). Les experts de chaque fonction répondent
  sans cesse aux mêmes questions.
- Les assistants IA grand public sont **inutilisables** dans ce contexte :
  1. les données ne peuvent pas sortir de l'entreprise (secret des affaires, RGPD, politique de sécurité, obligations sectorielles) ;
  2. ils **inventent** des réponses plausibles — inacceptable quand la réponse engage la responsabilité ;
  3. ils ne laissent **aucune trace auditable** de ce qui a été demandé et répondu.
- La recherche plein texte classique ne comprend pas les questions en langage naturel.

## 3. Cible

**Produit horizontal** : toute entreprise ou organisation francophone (PME, ETI, grands groupes,
secteur public) qui veut interroger ses propres documents sans les confier à un service grand
public. Le produit ne contient aucune logique propre à un métier ; l'adaptation passe par la
configuration (collections, droits, contexte métier `ASSISTANT_CONTEXT`, modèle).

| Persona | Rôle | Besoin principal |
|---|---|---|
| **Collaborateur** (RH, support, juriste, technicien, commercial…) | Utilisateur quotidien | Réponse rapide, sourcée, vérifiable en un clic |
| **Responsable de fonction** (DRH, responsable support, qualité, conformité…) | Sponsor, propriétaire d'une collection | Fiabilité, refus plutôt qu'invention, moins de sollicitations répétitives |
| **RSSI / DSI** | Décideur technique | Données qui ne sortent pas, SSO, droits, journal d'audit, installation maîtrisée |
| **Administrateur ContextHub** | Exploitant | Gestion des utilisateurs, des collections, des sauvegardes |

Cas d'usage types :

| Fonction | Corpus |
|---|---|
| Ressources humaines | Convention collective, accords d'entreprise, règlement intérieur, guides RH |
| Support et IT | Procédures, base de connaissances, documentation produit, PSSI |
| Juridique et achats | Contrats, conditions générales, politiques |
| Qualité, industrie, santé | Normes, modes opératoires, fiches techniques |
| Commercial et avant-vente | Offres, fiches produits, réponses à appels d'offres |
| Conformité, risques, finance | Réglementation européenne et nationale, doctrine, procédures de contrôle |

Les secteurs où la traçabilité est une obligation (finance, santé, énergie, secteur public) sont
les plus sensibles à l'argument souveraineté + audit ; ils servent de premières références, sans
que le produit leur soit réservé.

## 4. Proposition de valeur

1. **Souveraineté** — installation sur l'infrastructure du client ; mode 100 % local sans aucun flux sortant.
2. **Ancrage vérifiable** — chaque affirmation est rattachée à une source numérotée `[n]`, ouvrable à la page exacte.
3. **Refus explicite** — si les sources ne couvrent pas la question, réponse standard de refus, sans appel au LLM quand rien n'est pertinent.
4. **Auditabilité** — journal d'audit horodaté, chaîné (inviolabilité vérifiable), exportable.
5. **Générique** — aucun métier codé en dur : un même produit pour les RH, l'IT, le juridique, la qualité, la conformité.
6. **Open source (MIT)** — code auditable par le client, sans dépendance à un éditeur.

## 5. Périmètre v1

### 5.1 Exigences fonctionnelles

#### Authentification & accès (AUTH)
| ID | Exigence | Priorité |
|---|---|---|
| AUTH-1 | Comptes locaux (email + mot de passe ≥ 12 caractères, haché bcrypt) | Must |
| AUTH-2 | SSO OIDC générique (Entra ID, Google Workspace, Okta, Keycloak) avec provisioning automatique | Must |
| AUTH-3 | Rôles globaux : `admin`, `member` | Must |
| AUTH-4 | Droits par collection : `viewer` (interroger), `editor` (+ ajouter/supprimer des documents) ; collections ouvertes à tous les membres en option | Must |
| AUTH-5 | Session par cookie httpOnly `SameSite=Strict`, expiration configurable | Must |
| AUTH-6 | Clés d'API personnelles (hachées) pour intégrations | Should |
| AUTH-7 | Limitation des tentatives de connexion | Must |
| AUTH-8 | Création du premier administrateur au démarrage ou en CLI | Must |
| AUTH-9 | Droits par groupe de l'annuaire : une collection peut être ouverte à un groupe SSO (lecture ou modification) ; l'appartenance est relue à chaque connexion | Should |

#### Base documentaire (DOC)
| ID | Exigence | Priorité |
|---|---|---|
| DOC-1 | Import PDF, DOCX, TXT, Markdown, HTML, images JPG / PNG / TIFF multipage ; ingestion asynchrone avec progression | Must |
| DOC-2 | Documents numérisés (ADR-003) : OCR local (Tesseract, français + anglais) des images et des pages de PDF sans couche texte exploitable, page par page ; orientation, redressement et mise à l'échelle ; confiance mesurée par page, pages mal lues signalées (API et interface) ; file de traitement dédiée qui ne retarde pas les documents texte ; relance de l'OCR par un éditeur | Should |
| DOC-3 | Déduplication par empreinte SHA-256 au sein d'une collection | Must |
| DOC-4 | Découpage tenant compte de la structure : titres Word et Markdown, Titre / Chapitre / Section / Article | Must |
| DOC-10 | Reconnaissance des actes européens et de leur nom d'usage, transverses (RGPD, AI Act, NIS 2…) et sectoriels (MiFID II, DORA…) | Should |
| DOC-11 | Découpage des définitions : un passage par définition (articles « Définitions », glossaires, lexiques), phrase d'introduction répétée, terme dans le chemin de section | Should |
| DOC-12 | Connecteurs à chaud (Confluence Cloud / Data Center, Notion) : l'utilisateur joint des pages à une conversation, par lien ou par recherche filtrée (mots, espace, étiquette) ; aucune synchronisation en masse ; les pages sont supprimées avec la conversation ; la citation ouvre la page dans son outil d'origine | Should |
| DOC-13 | Documents techniques (manuels, procédures, fiches produit) : une page PDF dont la couche texte est inexploitable (glyphes sans table Unicode, étiquettes d'un schéma dont le texte est une image) est lue par OCR comme une page vide (`OCR_MIN_TEXT_QUALITY`) ; découpage par sections numérotées (« 3.2.2 Titre »), paragraphes, tableaux et légendes, passages adaptés (`CHUNK_SIZE_TECHNICAL`) ; titre du document et chemin de section en tête de chaque passage indexé et transmis au LLM, sans changer les citations ; en-têtes et pieds de page répétés retirés des passages ; découpage juridique inchangé | Should |
| DOC-5 | Suppression d'un document (fichier, vecteurs, métadonnées) | Must |
| DOC-6 | Suppression d'une collection complète | Must |
| DOC-7 | Ingestion idempotente : un retry ne crée jamais de doublons | Must |
| DOC-8 | Import d'URL : désactivé par défaut, protégé contre le SSRF | Should |
| DOC-9 | Limites : taille max par fichier, vérification du type réel (magic bytes), nom de fichier jamais utilisé sur disque | Must |

#### Questions / réponses (QA)
| ID | Exigence | Priorité |
|---|---|---|
| QA-1 | Recherche hybride : vecteurs (bge-m3) + plein texte PostgreSQL (français sans accents, préfixes), fusion RRF | Must |
| QA-2 | Seuil de distance : refus sans appel au LLM si aucune source n'est pertinente | Must |
| QA-3 | Citations numérotées `[n]` dans la réponse, liées aux sources | Must |
| QA-4 | Sources : nom du document, page, section/article, extrait | Must |
| QA-5 | Visionneuse : ouverture du document à la page citée | Must |
| QA-6 | Historique de conversations par utilisateur, questions de suivi (reformulation autonome) | Must |
| QA-7 | Choix du modèle parmi une liste autorisée par l'administrateur ; indication claire « local » / « cloud » | Must |
| QA-8 | Réponses en streaming | Must |
| QA-9 | Retour utilisateur 👍 / 👎 sur chaque réponse | Should |
| QA-10 | Re-ranking optionnel (cross-encoder) | Could |
| QA-11 | Contexte métier configurable par instance (`ASSISTANT_CONTEXT`), sans jamais lever les règles d'ancrage | Should |
| QA-13 | Références produit : identifiants de modèle extraits de chaque document (y compris la notation factorisée `US30010/15/20/30/40CNG` → US30010CNG … US30040CNG), conservés sur le document et en tête de ses passages ; une question qui cite une référence favorise les documents qui la portent et écarte les passages d'un autre produit ; si toutes les sources concernent un autre produit, la réponse le dit (refus `NO_ANSWER_TEXT` suivi de la mention des produits trouvés, sans appel au LLM) ; sans effet pour une question sans référence. Générique, aucune règle propre à un fabricant | Should |
| QA-14 | Passages voisins : autour de chaque passage retenu, les passages adjacents du même document et de la même section (pages voisines) rejoignent le contexte, dans un budget configurable (`RETRIEVAL_NEIGHBOURS`, `RETRIEVAL_CONTEXT_CHARS`), pour qu'une procédure sur plusieurs pages soit lue entière ; ils ne décident jamais de l'ancrage | Should |
| QA-15 | Diagnostic de récupération : `python -m src.cli explain-retrieval` liste, pour une question, chaque candidat (document, page, section, distance, rangs vecteur et plein texte, rapport à la référence citée, rang final, voisins) et la raison de son exclusion, sur la base réelle comme en test, par le même code que le chat | Should |
| QA-12 | Recherche agentique : le modèle décide si la question demande plusieurs recherches (une par sujet, document ou étape), les lance et fusionne les résultats de façon équilibrée ; une question sans passage pertinent reste refusée sans appel au LLM | Should |

#### Fournisseurs LLM (LLM)
| ID | Exigence | Priorité |
|---|---|---|
| LLM-1 | Ollama (local, défaut) | Must |
| LLM-2 | Anthropic, OpenAI | Must |
| LLM-3 | Endpoint compatible OpenAI (Mistral, vLLM, LM Studio, Azure via passerelle…) | Must |
| LLM-4 | Changement de fournisseur par configuration, sans modification de code | Must |

#### Audit & conformité (AUD)
| ID | Exigence | Priorité |
|---|---|---|
| AUD-1 | Journal de toutes les actions : connexions, questions/réponses (question, réponse, sources, modèle, latence), imports, suppressions, gestion des droits | Must |
| AUD-2 | Journal en ajout seul, chaîné par hachage SHA-256, avec vérification d'intégrité | Must |
| AUD-3 | Consultation filtrée et export CSV (admin) | Must |
| AUD-4 | Durées de conservation configurables (journal, conversations) et purge | Must |
| AUD-5 | Suppression / anonymisation d'un utilisateur (RGPD) | Must |

#### Administration (ADM)
| ID | Exigence | Priorité |
|---|---|---|
| ADM-1 | Gestion des utilisateurs (création, rôle, désactivation, suppression) | Must |
| ADM-2 | Gestion des droits par collection | Must |
| ADM-3 | État du système (base, Redis, LLM, worker) | Must |

#### Évaluation (EVAL)
| ID | Exigence | Priorité |
|---|---|---|
| EVAL-1 | Banc d'évaluation CLI sur jeu de référence JSON | Must |
| EVAL-2 | Métriques : exactitude du refus, score sémantique, taux de récupération de la bonne source, latence, calibration du score de pertinence | Must |
| EVAL-3 | Rapports JSON + CSV horodatés | Must |
| EVAL-4 | Jeu de référence hors finance livré avec son corpus (RH, Service-Public.fr), pour démontrer le caractère générique | Should |
| EVAL-5 | Jeu « manuels techniques » (pages attendues, pièges croisés entre modèles) sur un corpus fourni par l'utilisateur et vérifié par empreinte, et mesure de la récupération seule (sans LLM) | Should |

### 5.2 Exigences non fonctionnelles

| ID | Exigence |
|---|---|
| NFR-1 | **Sécurité** — aucune route métier accessible sans authentification ; erreurs génériques côté client (identifiant de corrélation) ; secrets obligatoires en production ; Redis protégé par mot de passe ; seul le reverse-proxy est exposé |
| NFR-2 | **Confidentialité** — traçage externe (LangSmith) désactivé par défaut ; en mode Ollama, aucun flux sortant |
| NFR-3 | **Installation** — `docker compose up -d` démarre la pile complète (proxy, frontend, API, worker, PostgreSQL, Redis, Ollama optionnel avec GPU) ; migrations automatiques |
| NFR-4 | **Exploitation** — sondes `/health` et `/ready`, métriques Prometheus `/metrics`, journaux structurés, scripts de sauvegarde / restauration |
| NFR-5 | **Qualité** — tests automatisés backend, build frontend, lint, en CI |
| NFR-6 | **Reproductibilité** — dépendances épinglées |
| NFR-7 | **Langue** — interface en français ; réponses dans la langue de la question |
| NFR-8 | **Performance** — première réponse en streaming < 5 s sur GPU 8 Go avec llama3.1:8b ; ingestion d'un PDF de 150 pages < 3 min |

### 5.3 Hors périmètre v1

- SaaS multi-tenant, facturation en ligne.
- Synchronisation en masse d'un espace documentaire externe (les connecteurs Confluence et Notion
  lisent des pages choisies, à la demande — DOC-12). Connecteurs SharePoint et Google Drive : candidats v1.1.
- Connexion à Confluence / Notion avec les droits de chaque utilisateur (OAuth par utilisateur) :
  en v1 la connexion utilise un compte de service défini par l'administrateur.
- Veille réglementaire automatique (synchronisation EUR-Lex / AMF) — candidat v1.2.
- Application mobile.
- Synchronisation SCIM (les groupes sont lus dans le jeton à chaque connexion SSO — AUTH-9 ; un
  départ du groupe prend effet à la connexion suivante, pas immédiatement).

## 6. Parcours clés

1. **Installation** — le DSI clone le dépôt, remplit `.env` (secrets), lance `docker compose up -d` ; un administrateur est créé au premier démarrage.
2. **Mise en place** — l'admin configure le SSO, crée une collection par fonction (« RH », « Support IT », « Contrats »), donne les droits à chaque équipe.
3. **Alimentation** — un éditeur de chaque équipe dépose ses documents ; suit la progression ; les doublons sont refusés.
4. **Usage** — un collaborateur pose une question, lit la réponse en streaming, clique sur `[2]` pour vérifier la page citée ; pose une question de suivi.
5. **Contrôle** — le responsable de la fonction ou le RSSI exporte le journal d'audit du mois et vérifie l'intégrité de la chaîne.
6. **Mesure** — avant mise en production, l'intégrateur lance le banc d'évaluation sur le corpus du client.

## 7. Modèle commercial

Le code est sous **licence MIT** : libre d'usage, de modification et d'intégration, y compris
dans un produit propriétaire. La valeur vendue est l'expertise et l'engagement, pas le droit
d'utiliser le logiciel :

- **Installation et mise en service** : déploiement chez le client, SSO, sauvegardes, dimensionnement GPU.
- **Évaluation et paramétrage** sur le corpus du client : jeu de questions avec ses experts, réglage de la recherche, choix du modèle.
- **Contrat de support** annuel : délais d'intervention garantis, mises à jour, correctifs de sécurité prioritaires.
- **Développements sur mesure** : connecteurs, intégrations, adaptations métier.
- **Formation** des administrateurs et des utilisateurs.

Risque assumé : un concurrent peut réutiliser le code. La différenciation repose sur la qualité
du service, la connaissance du produit et la relation client.

## 8. Indicateurs de succès

| Indicateur | Cible v1 |
|---|---|
| Exactitude du refus sur pièges du jeu de référence | 100 % |
| Taux de récupération de la bonne source (questions répondables) | ≥ 85 % |
| Score sémantique moyen | ≥ 0,80 |
| Temps d'installation d'une instance par un DSI | < 1 h |
| Pilotes signés | 3 |

## 9. Risques

| Risque | Mitigation |
|---|---|
| Qualité insuffisante d'un modèle 8B local sur des documents spécialisés | Banc d'évaluation sur le corpus du client ; option modèle plus grand ou cloud ; recherche hybride + découpage par section |
| Responsabilité en cas de réponse erronée | Citations obligatoires, refus explicite, mention « vérifiez les sources citées » dans l'interface |
| Code MIT réutilisable par un concurrent | Valeur portée par les services, le support et l'expertise ; rythme de mises à jour |
| Produit générique perçu comme « un RAG de plus » | Arguments différenciants : souveraineté, refus explicite mesuré, citations à la page, audit chaîné, droits par collection |
| Matériel GPU absent chez le client | Mode fournisseur cloud ou endpoint compatible OpenAI interne |

## 10. Qualité mesurée

Banc d'évaluation sur le jeu MiFID / MiFIR / EMIR (18 questions), 100 % local (qwen3.5:9b,
bge-m3), détail et méthode dans [evaluation.md](evaluation.md) :

| Indicateur (section 8) | Cible | Mesuré (2026-10-14) |
|---|---|---|
| Exactitude du refus sur pièges | 100 % | **100 %** ✅ |
| Taux de récupération de la bonne source | ≥ 85 % | **86 %** ✅ |
| Score sémantique moyen | ≥ 0,80 | **0,77** ⚠️ |
| Refus à tort (non ciblé) | — | 7 % |
| Bon article cité (non ciblé) | — | 90 % |

Le score de pertinence des sources ne sépare pas les réponses justes des fausses : il n'est pas
affiché aux utilisateurs (décision issue de la mesure).

### Manuels techniques, récupération seule (2026-11-22)

Jeu `golden_manuals.json` (recette Nitram), mesuré sans LLM dans la VM de développement
(`multilingual-e5-small` au lieu de `bge-m3`, plein texte en mémoire au lieu de PostgreSQL, OCR
anglais seulement) : bon document 93 % → 100 %, bonne page 67 % → 87 %, rang réciproque 0,31 →
0,53, passages d'un autre produit 34 % → 0 %, refus avec mention du produit pour le modèle absent.
Détail et limites dans [evaluation.md](evaluation.md). À refaire sur l'installation du client.

### Documents numérisés (ADR-003, 2026-11-18)

Corpus généré (`python -m eval.tools.make_scanned_corpus`, 5 fiches RH, 16 pages numérisées :
TIFF tourné de 90° à 200 dpi, PDF image retourné à 180° et incliné de 1,5° avec taches et flou,
PDF mixte, photo JPEG à 100 dpi, PNG incliné de 2,5° à 150 dpi), mesuré par
`python -m eval.tools.measure_ocr --lang eng` (Tesseract 4.1.1, **anglais seulement** : `fra`
n'était pas installé). Part des caractères de référence retrouvés, accents ignorés :

| | Avec prétraitement | Sans prétraitement |
|---|---|---|
| Ensemble (16 pages) | 99,3 % (98,6 % avec accents), confiance 90,4 | 53,2 %, confiance 66,3 |
| PDF retourné et incliné (8 pages) | 99,3 % | 9,0 % |
| TIFF tourné de 90° (3 pages) | 99,5 % | 94,7 % |
| JPEG 100 dpi, PNG incliné, PDF mixte | 98,1 à 99,8 % | 98,3 à 99,4 % |

Lecture : le gain vient surtout de l'orientation (pages retournées) ; sur les pages droites,
Tesseract seul lit déjà bien ce texte de synthèse (police nette, sans fond), plus facile que de
vrais scans. Les pages à couche texte du PDF mixte sont lues à 100 % sans OCR. **Non mesuré** :
la qualité en français (`fra`), le score de bout en bout du jeu `golden_rh_scans` avec un LLM
(`eval.run_eval`), de vrais documents numérisés.

## 11. Suivi d'implémentation

| Domaine | Statut | Détail |
|---|---|---|
| Licence MIT | ✅ | `LICENSE`, `CONTRIBUTING.md` (contributions sous MIT) |
| Positionnement générique | ✅ | Prompt système neutre, contexte métier configurable (`ASSISTANT_CONTEXT`), textes européens transverses reconnus (RGPD, AI Act, NIS 2, Data Act, CSRD…) |
| Configuration centralisée | ✅ | `src/config.py`, garde-fous de production, traçage externe désactivé par défaut |
| AUTH-9 Droits par groupe SSO | ✅ | `hub_collection_group_grants`, groupes du jeton conservés sur le compte (`hub_users.oidc_groups`) et relus à chaque connexion, meilleur niveau entre droit nominatif et droit de groupe, écran *Administration › Accès*, audit `grant.group_set` / `grant.group_revoke` ; Keycloak local avec comptes de démonstration (`deploy/keycloak/`, profil compose `sso`) et test e2e de connexion |
| AUTH-1 à AUTH-8 | ✅ | Comptes, SSO OIDC + PKCE, rôles, droits par collection, cookie strict + CSRF, clés d'API, limitation de débit, admin initial |
| DOC-11 Découpage des définitions | ✅ | `definition_items()` dans `src/ingestion/chunking.py` : listes numérotées (« 8) «terme», … ») et à puces (« - **Terme** : … »), sous-points rattachés ; `CHUNK_DEFINITIONS` ; documents existants : `cli reindex` |
| DOC-12 Connecteurs à chaud | ✅ | `src/connectors/` (Confluence REST v1 + CQL, Notion API), connexions gérées par l'administrateur (jeton chiffré, jamais renvoyé), pages jointes dans une « collection de session » privée à la conversation (`hub_collections.conversation_id`), périmètre « Pages jointes uniquement », citations ouvrant la page d'origine, journal d'audit (`connection.*`, `source.attach`, `source.detach`) ; test e2e avec un faux Confluence (`tests/e2e/mock_confluence.py`) |
| DOC-2 Documents numérisés (ADR-003, lot 3) | ✅ | **Livré (PR #9) et validé en recette le 21/11/2026 : PDF scanné en français lu et indexé (Tesseract `fra`). Mesure chiffrée en français à faire (§10 : anglais seulement).** `src/ocr/` (port `OcrEngine`, adaptateur Tesseract local, prétraitement Pillow : orientation OSD vérifiée par une seconde lecture si incertaine, agrandissement, redressement ±5°, pages blanches), import JPG/PNG/TIFF (signature binaire, une page citée par image TIFF, aperçu PNG `GET /documents/{id}/pages/{n}`), OCR page par page des PDF mixtes (`OCR_MIN_PAGE_CHARS`), qualité stockée (`read_mode`, `ocr_pages`, `ocr_confidence`, `ocr_low_pages`, migration `69ab4257`), file Celery `ocr` et worker dédié (`python -m src.workers.ocr_worker`, service `worker-ocr`), relance `POST /documents/{id}/ocr` (éditeur, audit `document.ocr`), contrôle « OCR » dans *Administration › Système* ; jeu `eval/datasets/golden_rh_scans.json` (20 questions dont 4 pièges) et mesures en §10 |
| DOC-1 à DOC-10 | ✅ | PDF/DOCX/TXT/MD/HTML (+ images, voir DOC-2), dédup SHA-256, découpage par article, suppressions, idempotence, URL anti-SSRF, limites, actes européens |
| QA-1 à QA-9 | ✅ | Hybride, refus court-circuité, citations `[n]`, visionneuse à la page, conversations + reformulation, liste blanche de modèles avec alerte cloud, streaming, retours 👍/👎 |
| QA-12 Recherche agentique | ✅ | Nœud `plan` après `retrieve` : décomposition en sous-requêtes (`AGENTIC_MAX_QUERIES`), recherches en parallèle, fusion par alternance ; requêtes lancées renvoyées par l'API et inscrites au journal d'audit |
| QA-10 Re-ranking | 🟡 | Implémenté en option (`RERANKER=cross_encoder`, `requirements-rerank.txt`), non mesuré |
| LLM-1 à LLM-4 | ✅ | Ollama, Anthropic, OpenAI, endpoint compatible OpenAI (Mistral, vLLM) |
| Passerelle LLM et consommation (ADR-001, lot 1) | ✅ | `LLM_GATEWAY_*` : tous les appels de modèle via une passerelle compatible OpenAI (LiteLLM), attribués à l'UUID de l'utilisateur et à l'étape ; modèles `gateway:<nom>` ; refus de la passerelle (budget 402, débit 429, indisponibilité 503/502) affichés dans le chat, encart « Budget atteint » ; pages *Ma consommation* (période, coût, requêtes, jetons, budget, évolution par jour, par modèle) et *Administration › Consommation* (par utilisateur, export CSV) lues à la passerelle, aucune donnée de coût stockée ; LiteLLM de développement (`deploy/litellm/`, profil compose `gateway`). Validé en recette le 21/11/2026 contre la passerelle LiteLLM réelle (consommation par utilisateur et globale) |
| Modèles et budgets pilotés depuis l'administration (ADR-002, lot 2) | 🟡 | **Validé en recette le 21/11/2026 contre LiteLLM réel : un budget atteint bloque l'utilisateur. Reste à vérifier : l'accès aux modèles locaux une fois le budget atteint (comportement attendu à décider).** *Administration des modèles* : table `hub_settings` (migration `69ab4256`, absence signalée dans *Administration › Système*), `GET/PUT/DELETE /admin/models` (modèles proposés et modèle par défaut choisis dans le catalogue disponible : modèles servis par la passerelle, sinon `LLM_MODELS` ; priorité au réglage sur l'environnement ; état `available`/`unavailable`/`unknown` identique dans `/models` ; lien vers la console de la passerelle quand il est ouvrable depuis un navigateur), modèle retiré refusé par `/chat/ask` et `/chat/stream`, y compris pour une conversation existante. *Budgets par utilisateur* : `GET/PUT/DELETE /admin/users/{id}/budget` (jour, semaine, 30 jours ; devise de la passerelle), écrits à la passerelle sur un budget dédié `contexthub-<uuid>`, jamais de budget de console modifié (retrait refusé, 409) ; budgets dans `/admin/usage` (lecture bornée, échec partiel signalé) ; `LLM_GATEWAY_KIND` validé au démarrage. *Audit* : `settings.models_set`, `settings.models_reset`, `budget.set`, `budget.remove` (ancienne et nouvelle valeur dans la même forme, utilisateur visé par son UUID, aucune dépense), chaîne vérifiée. Écrans d'administration livrés par le développeur interface. Reste : essai de bout en bout avec la passerelle de développement (`deploy/litellm/`) et tests PostgreSQL |
| AUD-1 à AUD-5 | ✅ | Journal chaîné SHA-256, vérification (UI + CLI), export CSV, rétention, effacement RGPD sans rupture de chaîne |
| ADM-1 à ADM-3 | ✅ | Utilisateurs, accès par collection, état du système et flux de données |
| EVAL-1 à EVAL-3 | ✅ | `eval/`, métriques dont calibration, rapports JSON + CSV |
| EVAL-4 Jeu RH | ✅ | `eval/datasets/golden_rh.json` (20 questions dont 5 pièges), instantané `eval/corpora/rh` (Etalab 2.0), outil de rafraîchissement `eval/tools/fetch_service_public.py`, indexation automatique du corpus ; « bon article » étendu aux titres de section |
| Fiabilité des documents techniques (DOC-13, QA-13 à QA-15, EVAL-5 ; recette Nitram) | 🟡 | **Implémenté et testé sur SQLite et doublures (380 tests) ; récupération mesurée dans la VM de développement avec un modèle d'embedding de substitution, pas sur l'installation du client.** Diagnostic `cli explain-retrieval` (`src/rag/retrieval.py`, même code que le nœud `retrieve`) ; couche texte inexploitable lue par OCR (`text_quality`, `OCR_MIN_TEXT_QUALITY`) ; références produit (`src/ingestion/references.py`, notation factorisée, `hub_documents.product_refs`, migration `69ab4258`, exposées par `GET /documents/{id}`), recherche limitée aux documents qui portent la référence et `RETRIEVAL_REFERENCE_BOOST` ; titre de contexte et en-têtes/pieds de page (`src/ingestion/layout.py`) ; découpage technique (`CHUNK_SIZE_TECHNICAL`), lignes de sommaire et colonnes « # » des tableaux PDF ignorées comme titres, `page_end` des passages ; passages voisins (`RETRIEVAL_NEIGHBOURS`, `RETRIEVAL_CONTEXT_CHARS`) ; garde-fou produit (`PRODUCT_MISMATCH_TEXT`, sans LLM) ; jeu `eval/datasets/golden_manuals.json` (20 questions dont 5 pièges) et `eval.tools.measure_retrieval`. Mesure avant/après dans [evaluation.md](evaluation.md) : bonne page 67 % → 87 %, passages d'un autre produit 34 % → 0 %, MAN01 retrouve les pages 42-44. **Non vérifié** : PostgreSQL (plein texte des références, requêtes `fetch` et filtre par document), `bge-m3`, OCR en français, LLM réel de bout en bout, affichage des références et des voisins dans l'interface (non modifiée). Les documents déjà indexés doivent être réindexés (`alembic upgrade head` puis `python -m src.cli reindex --collection …`) |
| NFR-1 Sécurité | ✅ | Voir `docs/security.md` |
| NFR-2 Confidentialité | ✅ | Mode 100 % local vérifié ; polices auto-hébergées, CSP sans ressource externe |
| NFR-3 Installation | ✅ | `docker compose up -d` testé de bout en bout (build, migrations, nginx, indexation, streaming SSE) |
| NFR-4 Exploitation | ✅ | `/health`, `/ready`, `/metrics`, logs JSON, sauvegarde + restauration testées |
| NFR-5 Qualité | ✅ | 150 tests (SQLite + PostgreSQL/pgvector réel) et 3 scénarios e2e Playwright, ruff, CI GitHub Actions |
| NFR-6 Reproductibilité | ✅ | Dépendances Python épinglées, `package-lock.json` |
| NFR-7 Langue | ✅ | Interface en français |
| NFR-9 Interface responsive (mobile, tablette) | ✅ | **Livrée (PR #10), vérifiée sur API simulée (360, 768, 1280 px) et validée en recette sur iPhone 14 Pro Max (Safari) le 21/11/2026. Chrome Android non testé.** Sous 768 px : barre supérieure (menu, titre de la page ou de la conversation, nouvelle question) et navigation en tiroir (dialogue Radix : piège de focus, Échap, clic extérieur, défilement bloqué) ; chat pleine largeur, saisie en bas (`100dvh`, zones de sécurité, champs en 16 px), sources en plein écran (feuille à droite sur tablette) ; listes principales en cartes (`table-cards`), autres tableaux défilant dans leur cadre (`TableScroll`) ; boutons d'au moins 40 px au toucher. Captures Playwright à 360, 768 et 1280 px : aucune page ne déborde horizontalement. Bureau (≥ 1024 px) inchangé, hormis les tableaux trop larges qui défilent désormais dans leur cadre et non plus avec toute la page. Non vérifié : Safari iOS et Chrome Android réels (clavier virtuel, encoche, PDF dans la visionneuse) |
| NFR-8 Performance | ⚠️ | Ingestion : 25 s pour 148 pages ✅. Réponse : non mesurée sur matériel cible (69 s sur GPU portable 4 Go partagé) |

## 12. Reste à faire avant la première vente

| Priorité | Action | Responsable |
|---|---|---|
| 🔴 | Faire rédiger CGV, contrat de support et DPA (sous-traitance RGPD) par un avocat | Fondateur |
| 🔴 | Mesurer la latence sur le matériel cible (GPU 16 Go, llama3.1:8b / qwen) et publier la configuration recommandée | Technique |
| 🟠 | Étendre le jeu de référence à 100 questions (avec des experts métier) ; atteindre ≥ 0,80 de score sémantique | Produit |
| 🟡 | Mesurer le re-classement (`RERANKER=cross_encoder`) | Technique |
| 🟠 | Test d'intrusion externe avant le premier client bancaire | Fondateur |
| 🟡 | Mentions légales, page de sécurité publique, modèle de devis et de contrat de support | Fondateur |
| 🟡 | v1.1 : connecteur SharePoint, connexion aux connecteurs avec les droits de chaque utilisateur (OAuth), synchronisation SCIM, export du journal vers SIEM | Technique |

## 13. Évolutions

Une évolution = une branche `feature/<nom>` issue de `master`, une pull request, sa documentation.

| Évolution | Branche | Statut |
|---|---|---|
| Recherche agentique (QA-12) | `feature/agentic-retrieval` | ✅ Livrée — mesures en §10 |
| Découpage des définitions point par point (DOC-11) | `feature/definition-chunking` | ✅ Livrée — mesures en §10 |
| Jeu de référence RH (EVAL-4) | `feature/hr-golden-dataset` | ✅ Livrée — mesures en §10 |
| Connecteurs à chaud Confluence et Notion (DOC-12) | `feature/document-connectors` | ✅ Livrée — pages jointes à la conversation, sans synchronisation |
| Droits par groupe SSO et Keycloak de développement (AUTH-9) | `feature/keycloak-dev-sso` | ✅ Livrée |
| Documents numérisés, OCR de bout en bout (DOC-2, ADR-003, lot 3) | `feature/ocr` | ✅ Livrée (PR #9) — mesure chiffrée en français à faire |
| Passerelle LLM et consommation (ADR-001, lot 1) | `feature/gateway-usage` | ✅ Livrée (PR #8) |
| Modèles et budgets depuis l'administration (ADR-002, lot 2) | `feature/models-budgets` | ✅ Livrée (PR #8) — modèles locaux à budget atteint à vérifier |
| Interface responsive (NFR-9) | `feature/responsive-ui` | ✅ Livrée (PR #10) |
| Fiabilité des documents techniques (DOC-13, QA-13 à QA-15, EVAL-5) | `feature/technical-docs-reliability` | 🟡 En revue — à mesurer sur l'installation du client (bge-m3, PostgreSQL, OCR `fra+eng`, LLM) |
