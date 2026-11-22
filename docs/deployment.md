# Installation et exploitation

Guide pour installer une instance ContextHub chez un client (on-premise, une instance par client).

## 1. Prérequis

| | Minimum | Recommandé |
|---|---|---|
| Système | Linux x86-64 avec Docker Engine 24+ et Docker Compose v2 | Ubuntu 24.04 LTS / RHEL 9 |
| CPU / RAM | 4 vCPU / 16 Go | 8 vCPU / 32 Go |
| GPU (LLM local) | — (mode CPU possible mais lent) | NVIDIA 16 Go+ (RTX 4000 Ada, L4, A10) |
| Disque | 50 Go | 200 Go SSD (modèles + documents + sauvegardes) |
| Réseau | Accès sortant pour télécharger images et modèles à l'installation | Aucun accès sortant ensuite en mode local |

Sans GPU, utilisez un fournisseur cloud ou un endpoint compatible OpenAI interne (vLLM) pour la
génération : sur CPU, un modèle 8B répond en 30 à 60 secondes.

## 2. Installation

```bash
git clone <dépôt> contexthub && cd contexthub
cp .env.production.example .env && chmod 600 .env
# Éditez .env : remplacez CHAQUE « change-me » (openssl rand -base64 48), PUBLIC_URL, ADMIN_EMAIL.

# Avec LLM local sur GPU NVIDIA :
docker compose -f docker-compose.yaml -f docker-compose.gpu.yaml --profile ollama up -d --build
docker compose exec ollama ollama pull bge-m3
docker compose exec ollama ollama pull llama3.1:8b

# Sans Ollama (fournisseur cloud ou endpoint OpenAI-compatible interne) :
docker compose up -d --build
```

L'API applique les migrations au démarrage et crée l'administrateur défini par `ADMIN_EMAIL` /
`ADMIN_PASSWORD`. **Retirez ensuite `ADMIN_PASSWORD` du fichier `.env`.**

Vérifications :

```bash
docker compose ps                                   # tous les services "healthy" / "running"
curl -fsS http://localhost:8080/api/ready           # {"status":"ready"}
```

Ouvrez `PUBLIC_URL`, connectez-vous, puis *Administration › Système* : chaque service doit être
« Opérationnel ».

## 3. TLS

- **Derrière le reverse-proxy de l'entreprise** (recommandé) : laissez `NGINX_CONF=http`, publiez
  `HTTP_PORT` vers le proxy uniquement (`HTTP_BIND=127.0.0.1` si le proxy est sur l'hôte).
- **TLS par le nginx fourni** : `NGINX_CONF=tls`, déposez `fullchain.pem` et `privkey.pem` dans
  `TLS_CERT_DIR`, ouvrez `HTTPS_PORT`.

Les cookies de session sont `Secure` : sans HTTPS de bout en bout, la connexion échoue. Pour un
essai sans TLS uniquement : `COOKIE_SECURE=false`.

**Réponses en streaming à travers un proxy.** `/api/chat/stream` est un flux Server-Sent Events.
Pendant la préparation d'une réponse (recherche, re-classement, planification), l'API envoie un
événement d'étape puis un commentaire `: ping` dès que `SSE_HEARTBEAT_SECONDS` (10 s) passent sans
autre envoi : un proxy qui coupe les réponses muettes (Cloudflare au bout de ~100 s, un répartiteur de
charge, un tunnel) ne coupe donc plus une réponse longue. Sur tout proxy placé devant ContextHub :
pas de mise en tampon des réponses `text/event-stream` (l'API envoie `X-Accel-Buffering: no`, le
nginx fourni a `proxy_buffering off`) et un délai d'inactivité nettement supérieur au battement
(`proxy_read_timeout 300s` dans le nginx fourni). `/api/chat/ask` (réponse en un bloc) n'a pas de
battement : derrière un tel proxy, préférez le flux pour les questions longues.

## 4. SSO (OpenID Connect)

1. Chez le fournisseur d'identité, créez une application web avec l'URI de redirection
   `https://<PUBLIC_URL>/api/auth/oidc/callback`, portées `openid email profile`.
2. Renseignez `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_LABEL`.
3. Optionnel : `OIDC_ALLOWED_DOMAINS=client.fr`, `OIDC_ADMIN_GROUPS=<id du groupe admin>`
   (Entra ID : activez la revendication `groups` dans le jeton).
4. `docker compose up -d api` puis testez. Une fois validé : `LOCAL_LOGIN_ENABLED=false`.
5. Droits par groupe : dans *Administration › Accès*, ouvrez une collection à un groupe de
   l'annuaire (nom transmis dans la revendication `OIDC_GROUPS_CLAIM`, `groups` par défaut ;
   Entra ID transmet l'identifiant du groupe). Les membres la trouvent à leur prochaine connexion.

Pour essayer le SSO sans annuaire : un Keycloak local avec des comptes de démonstration est
fourni (`deploy/keycloak/README.md`).

Exemples d'émetteur : Entra ID `https://login.microsoftonline.com/<tenant>/v2.0`, Google
`https://accounts.google.com`, Keycloak `https://sso.client.fr/realms/<realm>`.

## 5. Choix des modèles

`LLM_MODELS` est la liste blanche proposée aux utilisateurs (`fournisseur:modèle`, le premier est
le modèle par défaut) :

```ini
LLM_MODELS=ollama:llama3.1:8b                                   # 100 % local
LLM_MODELS=ollama:llama3.1:8b,anthropic:claude-sonnet-5-5       # local par défaut, cloud au choix
LLM_MODELS=openai_compatible:mistral-large-latest               # Mistral La Plateforme
LLM_MODELS=openai_compatible:Qwen2.5-32B-Instruct               # vLLM interne (+ OPENAI_COMPATIBLE_IS_LOCAL=true)
```

Changer de **modèle d'embedding** (`EMBEDDING_MODEL`) impose de réindexer :
`docker compose exec api entrypoint.sh cli reindex`. Si la **dimension** change aussi
(`EMBEDDING_DIM`), la colonne vectorielle doit d'abord être recréée :

```bash
docker compose exec postgres psql -U contexthub -c "DROP INDEX ix_hub_chunks_embedding; TRUNCATE hub_chunks; \
  ALTER TABLE hub_chunks ALTER COLUMN embedding TYPE vector(1536); \
  CREATE INDEX ix_hub_chunks_embedding ON hub_chunks USING hnsw (embedding vector_cosine_ops);"
docker compose exec api entrypoint.sh cli reindex
```

### Re-classement (optionnel)

`RERANKER=cross_encoder` re-classe les meilleurs candidats avec un cross-encoder local
(`BAAI/bge-reranker-v2-m3`, 568 M paramètres, ~2,3 Go, rien ne sort du serveur). L'image Docker
fournie ne l'embarque pas : installez `requirements-rerank.txt` (sentence-transformers et PyTorch)
dans l'image de l'API, avec **PyTorch CUDA** sur un serveur à GPU NVIDIA (roue CUDA de PyTorch,
NVIDIA Container Toolkit et réservation du GPU pour le service `api`, comme pour `ollama` dans
`docker-compose.gpu.yaml`). Téléchargez le modèle une fois (`HF_HUB_OFFLINE=false`), puis passez
`HF_HUB_OFFLINE=true`.

Le coût croît avec chaque paire (question, passage) lue par le modèle ; il est borné :

| Réglage | Défaut | Rôle |
|---|---|---|
| `RERANK_CANDIDATES` | 30 | Candidats re-classés par recherche (les meilleurs après fusion ; les autres gardent leur ordre derrière) |
| `RERANK_MAX_PAIRS` | 60 | Paires par réponse : la question en prend jusqu'à `RERANK_CANDIDATES`, les sous-requêtes du planificateur se partagent le reste (sous 2 chacune, elles ne sont pas re-classées) |
| `RERANKER_DEVICE` | `auto` | CUDA si PyTorch voit un GPU, sinon CPU ; ou `cpu`, `cuda:1`… |
| `RERANKER_THREADS` | 0 | Threads CPU (0 : choix de PyTorch, un par cœur physique) ; à fixer dans un conteneur limité en CPU |
| `RERANKER_BATCH_SIZE` | 16 | Paires par lot |
| `RERANKER_MAX_LENGTH` | 512 | Jetons lus par paire (le modèle en accepte 8 192 ; 512 suffit aux passages de ContextHub) |

Une paire déjà notée (même question, même passage) n'est pas renotée : une question reposée après
une erreur réseau ne refait pas ce travail. Le journal de l'API indique au chargement le périphérique
retenu et, pour chaque réponse, une ligne `[timing] request=… rerank=…s rerank_pairs=… rerank_cached=…`
(avec les durées de `condense`, `retrieve`, `plan`, `generate` et du premier jeton) ;
`python -m src.cli explain-retrieval` re-classe avec le même budget que la question du chat.

**Ordre de grandeur.** Sur CPU, compter de l'ordre d'une à quelques secondes par paire selon la
machine et la longueur des passages (calcul équivalent sur 2 vCPU : ~3,5 s par paire de 320 jetons ;
PRD §10) : 30 paires coûtent alors de l'ordre de la minute. Sur CPU, réduisez les bornes
(`RERANK_CANDIDATES=10`, `RERANK_MAX_PAIRS=10` : la question seule) ou laissez `RERANKER=none`. Sur
GPU, le re-classement de 60 paires est attendu bien en dessous de la seconde — **non mesuré à ce
jour**, à vérifier sur le matériel cible avec la ligne `[timing]`.

### Passerelle LLM

Pour suivre les coûts, fixer des budgets par utilisateur ou limiter le débit, placez une
passerelle compatible OpenAI devant les modèles (référence : LiteLLM, édition open source).
ContextHub lui envoie alors tous les appels de modèle et affiche la consommation qu'il y lit
(*Ma consommation*, *Administration › Consommation*) ; il n'en stocke rien.

```ini
LLM_GATEWAY_URL=http://litellm.interne:4000     # racine de la passerelle, sans /v1
LLM_GATEWAY_API_KEY=sk-…                        # clé applicative : appels de modèle, liste des modèles
LLM_GATEWAY_ADMIN_KEY=sk-…                      # facultatif : consommation et budgets (clé d'administrateur LiteLLM)
LLM_GATEWAY_LABEL=Passerelle IA du groupe       # libellé affiché aux utilisateurs
LLM_MODELS=gateway:gpt-4o,gateway:llama3.1:8b   # facultatif : modèles proposés tant que l'administration n'en a pas choisi
# LLM_GATEWAY_CONSOLE_URL=https://litellm.interne/ui   # facultatif : lien vers la console depuis Administration › Modèles
```

- Les identifiants de modèle deviennent `gateway:<nom servi par la passerelle>`. La passerelle
  est la source de vérité du catalogue (la portée de la clé applicative fait office de liste
  blanche) ; les entrées `gateway:` de `LLM_MODELS` le restreignent. Sans entrée `gateway:`,
  tous les modèles servis sont proposés, `LLM_MODEL_NAME` en tête. Les entrées d'autres
  fournisseurs sont ignorées (avertissement dans *Administration › Système*). Les embeddings
  ne passent pas par la passerelle.
- `LLM_GATEWAY_ADMIN_KEY` n'a pas de repli sur `LLM_GATEWAY_API_KEY` (moindre privilège) : sans
  elle, le chat fonctionne et les pages de consommation indiquent que le suivi n'est pas
  configuré.
- Sur `/chat/ask`, un refus de la passerelle porte l'en-tête `X-ContextHub-Error-Code`
  (`budget_exceeded`, `rate_limited`, `unreachable`, `unavailable`, `rejected`) : un client
  d'API distingue ainsi un 429 de la passerelle de la limitation de débit de ContextHub.
- La passerelle doit être joignable depuis le service `api` (réseau Docker ou réseau interne) ;
  `/admin/system` vérifie sa disponibilité (*Administration › Système*).
- Côté LiteLLM : base PostgreSQL dédiée (obligatoire pour le suivi des dépenses), suivi par
  client final actif, prompts non conservés (`store_prompts_in_spend_logs: false`).
- Modèles proposés (ADR-002) : l'administrateur les choisit, avec le modèle par défaut, dans
  *Administration › Modèles* (`/admin/models`) parmi les modèles servis par la passerelle
  (sans passerelle : parmi `LLM_MODELS`, dont les clés de fournisseur sont configurées). Ce
  réglage est stocké dans `hub_settings` et prime sur `LLM_MODELS` ; le supprimer rétablit le
  comportement d'environnement. Avec plusieurs workers d'API, un changement s'applique
  partout en 5 secondes au plus. Migration `69ab4256` requise (`alembic upgrade head`) : sans
  elle, le chat garde les valeurs d'environnement et *Administration › Système* affiche
  « Migration manquante » sur le contrôle de la base.
- Budgets par utilisateur (ADR-002) : fixés depuis l'administration (`/admin/users/{id}/budget`,
  jour, semaine ou 30 jours), écrits à la passerelle avec `LLM_GATEWAY_ADMIN_KEY`, qui doit être
  une clé d'administrateur complète (la suppression d'un budget est réservée au rôle
  `proxy_admin`). ContextHub crée pour chaque utilisateur un budget dédié `contexthub-<uuid>`
  rattaché au client final `<uuid>` ; il ne modifie jamais un budget défini dans la console
  (budget partagé, palier) : fixer un budget remplace le rattachement, retirer un budget de la
  console est refusé (à faire dans la console). Retirer le budget ContextHub ramène
  l'utilisateur au budget par défaut de la passerelle (`max_end_user_budget_id`) s'il en existe
  un. ContextHub ne stocke aucun budget.
- `LLM_GATEWAY_IS_LOCAL=true` seulement si tous les modèles servis restent dans votre réseau :
  sinon le chat signale que des extraits sont envoyés hors de l'infrastructure.

Une passerelle de développement prête à l'emploi (profil compose `gateway`) et la procédure de
vérification de bout en bout sont décrites dans `deploy/litellm/README.md`.

## 6. Documents numérisés (OCR)

Les images (JPG, PNG, TIFF multipage) et les pages de PDF sans couche texte exploitable sont
lues par Tesseract, sur le serveur (aucun moteur cloud). ADR-003.

- **Worker dédié** : l'OCR passe par la file Celery `ocr`, servie par le service `worker-ocr`
  (`python -m src.workers.ocr_worker`, `OCR_WORKER_CONCURRENCY` processus, 1 par défaut). Le
  service `worker` ne consomme que la file par défaut : un document texte importé pendant un
  long scan est indexé sans attendre. Un PDF commence sur la file par défaut ; s'il contient
  des pages à lire, il est transmis à la file `ocr` (statut « En attente » entre les deux).
  Sans worker OCR actif, *Administration › Système* le signale (contrôle « Workers »).
- **Langues** : `OCR_LANGUAGES=fra+eng` (paquets `tesseract-ocr-fra`, `tesseract-ocr-eng`,
  inclus dans l'image). Ajoutez un paquet `tesseract-ocr-<langue>` au `docker/Dockerfile` puis
  la langue à `OCR_LANGUAGES` (`fra+eng+deu`). Une langue configurée mais absente est ignorée
  et signalée dans *Administration › Système* (contrôle « OCR ») ; si aucune n'est installée,
  l'OCR est désactivé. Les données `osd` (`tesseract-ocr-osd`) servent à redresser les pages
  tournées de 90°, 180° ou 270°.
- **Qualité** : une page de PDF passe à l'OCR si sa couche texte compte moins de
  `OCR_MIN_PAGE_CHARS` lettres et chiffres (le texte natif est gardé quand l'OCR ne lit pas
  plus) ; pages rendues à `OCR_DPI` (300) ; les scans de faible résolution sont agrandis,
  redressés (±5°). Une page dont la confiance moyenne est inférieure à `OCR_MIN_CONFIDENCE`
  (60/100), en échec ou au-delà de `OCR_PAGE_TIMEOUT_SECONDS` (120 s) est signalée : le
  document passe en « Lecture partielle : pages … ». Les pages blanches ne sont pas signalées.
- **Relancer l'OCR** : bouton « Relancer l'OCR » de la Base documentaire (droits éditeur), ou
  `POST /api/documents/{id}/ocr` (`{"all_pages": true}` pour relire aussi les pages qui ont
  une couche texte). Utile après l'installation d'une langue. Les documents déjà indexés avant
  ce lot gardent leur lecture : relancez l'OCR ou `cli reindex` (qui lit les pages numérisées
  dans le processus de la commande).
- **Sans Tesseract** : l'application démarre, les documents texte s'indexent ; les pages
  numérisées sont signalées comme non lues et les images importées échouent avec un message
  explicite. Tesseract est détecté au démarrage de chaque processus : après installation,
  redémarrez `worker`, `worker-ocr` et `api`.
- **Matériel** : mesuré sur un poste de développement à 2 vCPU (Tesseract 4.1, anglais) :
  environ 5 s par page A4 à 300 dpi (détection d'orientation comprise), moins de 200 Mo de
  mémoire par processus ; une page retournée dont l'orientation est incertaine est lue deux
  fois. Prévoyez davantage avec deux langues (`fra+eng`). Pour de gros volumes de scans, augmentez
  `OCR_WORKER_CONCURRENCY` (un processus par cœur disponible, sans prendre les cœurs de l'API
  et du worker par défaut) ou lancez plusieurs services `worker-ocr`. Un document OCR est
  limité à 6 h au total.
- Développement : `start.sh` / `start.bat` lancent les deux workers ; Tesseract s'installe avec
  `apt install tesseract-ocr tesseract-ocr-fra tesseract-ocr-osd` (Windows : installeur UB
  Mannheim, `tesseract` dans le `PATH`).

## 7. Exploitation courante

| Tâche | Commande |
|---|---|
| Journaux | `docker compose logs -f api worker worker-ocr` (format JSON avec `LOG_FORMAT=json`) |
| Créer / promouvoir un administrateur | `docker compose exec api entrypoint.sh cli create-admin --email x@client.fr` |
| Vérifier l'intégrité du journal d'audit | `docker compose exec api entrypoint.sh cli verify-audit` |
| Appliquer les durées de conservation | automatique chaque nuit (service `beat`), ou `… cli purge` |
| Réindexer tous les documents | `docker compose exec api entrypoint.sh cli reindex [--collection NOM]` |
| Mesurer la qualité sur un corpus | `docker compose exec api entrypoint.sh eval --dataset … --collection …` |
| Supervision | `GET /api/ready` (sonde), `GET /metrics` sur le réseau interne (Prometheus) |

## 8. Sauvegarde et restauration

```bash
./scripts/backup.sh /srv/backups/contexthub          # base (pg_dump) + documents + sommes SHA-256
./scripts/restore.sh /srv/backups/contexthub 20261013-020000
```

Planifiez la sauvegarde (cron ou timer systemd) et copiez-la hors de l'hôte. Testez une
restauration à chaque montée de version majeure. Après restauration, vérifiez l'intégrité du
journal d'audit.

## 9. Mise à jour

```bash
./scripts/backup.sh
git pull && docker compose up -d --build              # les migrations s'appliquent au démarrage
```

Une évolution du découpage (par exemple le découpage des définitions point par point) ne
s'applique qu'aux documents importés ensuite. Pour en faire profiter les documents déjà
indexés : `docker compose exec api entrypoint.sh cli reindex [--collection NOM]`.

## 10. Mise à niveau depuis une version antérieure à la v1

La v1 change le stockage des passages indexés (table `hub_chunks`, découpage par article). Les
documents importés avant la v1 apparaissent en échec avec le message « Document importé avant la
v1 » : supprimez-les puis réimportez-les. Les anciennes tables `langchain_pg_*` peuvent être
supprimées une fois la réimportation terminée.

## 11. Dimensionnement indicatif

Mesures sur le corpus MiFID II / MiFIR / EMIR (272 pages, 1 240 passages) :

- indexation : 25 s pour MiFID II (148 pages) avec bge-m3 sur GPU ;
- réponse : dépend surtout du LLM et du GPU (voir [evaluation.md](evaluation.md)) ; avec
  `RERANKER=cross_encoder`, du re-classement (voir § 5, *Re-classement*), chaque étape étant
  chiffrée par la ligne `[timing]` du journal de l'API.
