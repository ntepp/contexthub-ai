# PRD — ContextHub AI v1

| | |
|---|---|
| **Statut** | v1.0 — implémentée, en recette (branche `feature/commercial-v1`) |
| **Auteur** | ntepp |
| **Dernière mise à jour** | 2026-10-14 |
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

#### Base documentaire (DOC)
| ID | Exigence | Priorité |
|---|---|---|
| DOC-1 | Import PDF, DOCX, TXT, Markdown, HTML ; ingestion asynchrone avec progression | Must |
| DOC-2 | OCR des pages sans couche texte (Tesseract, français + anglais) | Should |
| DOC-3 | Déduplication par empreinte SHA-256 au sein d'une collection | Must |
| DOC-4 | Découpage tenant compte de la structure : titres Word et Markdown, Titre / Chapitre / Section / Article | Must |
| DOC-10 | Reconnaissance des actes européens et de leur nom d'usage, transverses (RGPD, AI Act, NIS 2…) et sectoriels (MiFID II, DORA…) | Should |
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
- Connecteurs (SharePoint, Confluence, Google Drive) — candidats v1.1.
- Veille réglementaire automatique (synchronisation EUR-Lex / AMF) — candidat v1.2.
- Application mobile.
- Groupes / synchronisation SCIM.

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

## 11. Suivi d'implémentation

| Domaine | Statut | Détail |
|---|---|---|
| Licence MIT | ✅ | `LICENSE`, `CONTRIBUTING.md` (contributions sous MIT) |
| Positionnement générique | ✅ | Prompt système neutre, contexte métier configurable (`ASSISTANT_CONTEXT`), textes européens transverses reconnus (RGPD, AI Act, NIS 2, Data Act, CSRD…) |
| Configuration centralisée | ✅ | `src/config.py`, garde-fous de production, traçage externe désactivé par défaut |
| AUTH-1 à AUTH-8 | ✅ | Comptes, SSO OIDC + PKCE, rôles, droits par collection, cookie strict + CSRF, clés d'API, limitation de débit, admin initial |
| DOC-1 à DOC-10 | ✅ | PDF/DOCX/TXT/MD/HTML, OCR Tesseract, dédup SHA-256, découpage par article, suppressions, idempotence, URL anti-SSRF, limites, actes européens |
| QA-1 à QA-9 | ✅ | Hybride, refus court-circuité, citations `[n]`, visionneuse à la page, conversations + reformulation, liste blanche de modèles avec alerte cloud, streaming, retours 👍/👎 |
| QA-10 Re-ranking | 🟡 | Implémenté en option (`RERANKER=cross_encoder`, `requirements-rerank.txt`), non mesuré |
| LLM-1 à LLM-4 | ✅ | Ollama, Anthropic, OpenAI, endpoint compatible OpenAI (Mistral, vLLM) |
| AUD-1 à AUD-5 | ✅ | Journal chaîné SHA-256, vérification (UI + CLI), export CSV, rétention, effacement RGPD sans rupture de chaîne |
| ADM-1 à ADM-3 | ✅ | Utilisateurs, accès par collection, état du système et flux de données |
| EVAL-1 à EVAL-3 | ✅ | `eval/`, métriques dont calibration, rapports JSON + CSV |
| NFR-1 Sécurité | ✅ | Voir `docs/security.md` |
| NFR-2 Confidentialité | ✅ | Mode 100 % local vérifié ; polices auto-hébergées, CSP sans ressource externe |
| NFR-3 Installation | ✅ | `docker compose up -d` testé de bout en bout (build, migrations, nginx, indexation, streaming SSE) |
| NFR-4 Exploitation | ✅ | `/health`, `/ready`, `/metrics`, logs JSON, sauvegarde + restauration testées |
| NFR-5 Qualité | ✅ | 93 tests (SQLite + PostgreSQL/pgvector réel), ruff, CI GitHub Actions |
| NFR-6 Reproductibilité | ✅ | Dépendances Python épinglées, `package-lock.json` |
| NFR-7 Langue | ✅ | Interface en français |
| NFR-8 Performance | ⚠️ | Ingestion : 25 s pour 148 pages ✅. Réponse : non mesurée sur matériel cible (69 s sur GPU portable 4 Go partagé) |

## 12. Reste à faire avant la première vente

| Priorité | Action | Responsable |
|---|---|---|
| 🔴 | Faire rédiger CGV, contrat de support et DPA (sous-traitance RGPD) par un avocat | Fondateur |
| 🟠 | Constituer un jeu de référence hors finance (RH ou IT) pour démontrer le caractère générique | Produit |
| 🔴 | Mesurer la latence sur le matériel cible (GPU 16 Go, llama3.1:8b / qwen) et publier la configuration recommandée | Technique |
| 🟠 | Étendre le jeu de référence à 100 questions (avec des experts métier) ; atteindre ≥ 0,80 de score sémantique | Produit |
| 🟠 | Découper les articles de définitions point par point ; décomposer les questions multi-textes ; mesurer le re-classement | Technique |
| 🟠 | Test d'intrusion externe avant le premier client bancaire | Fondateur |
| 🟡 | Mentions légales, page de sécurité publique, modèle de devis et de contrat de support | Fondateur |
| 🟡 | v1.1 : connecteurs SharePoint / Confluence, groupes SSO → droits, export du journal vers SIEM | Technique |
