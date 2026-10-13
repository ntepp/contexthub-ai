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

## 4. SSO (OpenID Connect)

1. Chez le fournisseur d'identité, créez une application web avec l'URI de redirection
   `https://<PUBLIC_URL>/api/auth/oidc/callback`, portées `openid email profile`.
2. Renseignez `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_LABEL`.
3. Optionnel : `OIDC_ALLOWED_DOMAINS=client.fr`, `OIDC_ADMIN_GROUPS=<id du groupe admin>`
   (Entra ID : activez la revendication `groups` dans le jeton).
4. `docker compose up -d api` puis testez. Une fois validé : `LOCAL_LOGIN_ENABLED=false`.

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

## 6. Exploitation courante

| Tâche | Commande |
|---|---|
| Journaux | `docker compose logs -f api worker` (format JSON avec `LOG_FORMAT=json`) |
| Créer / promouvoir un administrateur | `docker compose exec api entrypoint.sh cli create-admin --email x@client.fr` |
| Vérifier l'intégrité du journal d'audit | `docker compose exec api entrypoint.sh cli verify-audit` |
| Appliquer les durées de conservation | automatique chaque nuit (service `beat`), ou `… cli purge` |
| Réindexer tous les documents | `docker compose exec api entrypoint.sh cli reindex [--collection NOM]` |
| Mesurer la qualité sur un corpus | `docker compose exec api entrypoint.sh eval --dataset … --collection …` |
| Supervision | `GET /api/ready` (sonde), `GET /metrics` sur le réseau interne (Prometheus) |

## 7. Sauvegarde et restauration

```bash
./scripts/backup.sh /srv/backups/contexthub          # base (pg_dump) + documents + sommes SHA-256
./scripts/restore.sh /srv/backups/contexthub 20261013-020000
```

Planifiez la sauvegarde (cron ou timer systemd) et copiez-la hors de l'hôte. Testez une
restauration à chaque montée de version majeure. Après restauration, vérifiez l'intégrité du
journal d'audit.

## 8. Mise à jour

```bash
./scripts/backup.sh
git pull && docker compose up -d --build              # les migrations s'appliquent au démarrage
```

## 9. Mise à niveau depuis une version antérieure à la v1

La v1 change le stockage des passages indexés (table `hub_chunks`, découpage par article). Les
documents importés avant la v1 apparaissent en échec avec le message « Document importé avant la
v1 » : supprimez-les puis réimportez-les. Les anciennes tables `langchain_pg_*` peuvent être
supprimées une fois la réimportation terminée.

## 10. Dimensionnement indicatif

Mesures sur le corpus MiFID II / MiFIR / EMIR (272 pages, 1 240 passages) :

- indexation : 25 s pour MiFID II (148 pages) avec bge-m3 sur GPU ;
- réponse : dépend surtout du LLM et du GPU (voir [evaluation.md](evaluation.md)).
