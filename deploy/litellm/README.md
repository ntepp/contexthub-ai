# Passerelle LLM de développement (LiteLLM)

Une passerelle LiteLLM locale pour essayer le suivi de la consommation, les budgets par
utilisateur et les pages *Ma consommation* et *Administration › Consommation*.
**Développement uniquement** : clé maître, clé de chiffrement et mot de passe de base fixes
(marqués `dev-only`), pas de TLS. Les clés des fournisseurs (OpenAI, Anthropic) sont lues dans
l'environnement et ne sont écrites dans aucun fichier du dépôt.

Principe (ADR-001) : ContextHub envoie tous ses appels de modèle à la passerelle, avec
l'identifiant (UUID) de l'utilisateur et l'étape du pipeline en en-têtes ; la passerelle
calcule les coûts, applique budgets et limites de débit, et ContextHub lit la consommation
chez elle. ContextHub ne stocke aucune donnée de coût.

| Élément | Valeur |
|---|---|
| Image | `ghcr.io/berriai/litellm:v1.106.6` (version épinglée, voir `docker-compose.dev.yaml`) |
| Adresse | http://127.0.0.1:4000 (publiée sur la boucle locale uniquement) |
| Interface d'administration LiteLLM | http://127.0.0.1:4000/ui (connexion avec la clé maître) |
| Clé maître (dev) | `sk-contexthub-dev-only-master-key` |
| Base de données | service `litellm-db` (PostgreSQL 16), distinct de la base de ContextHub, non publié |
| Modèles | `deploy/litellm/config.yaml` : `llama3.1:8b` (Ollama local), `gpt-4o` (OpenAI), `claude-sonnet-5-5` (Anthropic) |

## 1. Démarrer

Les modèles cloud ne fonctionnent que si leur clé est présente dans l'environnement du shell
ou dans le `.env` à la racine du dépôt (lu par Docker Compose) :

```bash
# facultatif : export OPENAI_API_KEY=…  /  export ANTHROPIC_API_KEY=…
docker compose -f docker-compose.dev.yaml --profile gateway up -d
curl -s http://127.0.0.1:4000/health/liveliness      # répond dès que la passerelle est prête
```

Le modèle local passe par l'Ollama de l'hôte (`http://host.docker.internal:11434`). Sous
Linux, Ollama n'écoute par défaut que sur `127.0.0.1` : lancez-le avec `OLLAMA_HOST=0.0.0.0`
ou indiquez une autre adresse dans `LITELLM_OLLAMA_API_BASE`.

## 2. Créer la clé applicative de ContextHub

ContextHub utilise deux clés : une **clé applicative** (moindre privilège) pour les appels de
modèle et la liste des modèles, et une **clé d'administrateur de la passerelle**
(`LLM_GATEWAY_ADMIN_KEY`) pour lire la consommation. LiteLLM réserve la lecture de l'activité
par utilisateur (`/customer/daily/activity`) aux clés d'administrateur : en développement,
c'est donc la clé maître. Il n'y a pas de repli sur la clé applicative : sans
`LLM_GATEWAY_ADMIN_KEY`, le chat fonctionne, les pages de consommation indiquent que le suivi
n'est pas configuré et *Administration › Système* le signale.

```bash
curl -s http://127.0.0.1:4000/key/generate \
  -H "Authorization: Bearer sk-contexthub-dev-only-master-key" \
  -H "Content-Type: application/json" \
  -d '{"key_alias": "contexthub-dev", "models": ["qwen3.5:9b", "llama3.1:8b", "gpt-4o", "claude-sonnet-5-5"]}'
```

La réponse contient `"key": "sk-…"` : c'est la clé applicative.

## 3. Configurer ContextHub (`.env`)

```ini
LLM_GATEWAY_URL=http://127.0.0.1:4000
LLM_GATEWAY_API_KEY=sk-…                                # clé applicative de l'étape 2
LLM_GATEWAY_ADMIN_KEY=sk-contexthub-dev-only-master-key # lecture de la consommation (dev uniquement)
LLM_GATEWAY_LABEL=LiteLLM
# LLM_GATEWAY_IS_LOCAL=true     # seulement si TOUS les modèles servis restent dans votre réseau
# LLM_MODELS=gateway:llama3.1:8b,gateway:gpt-4o   # facultatif : restreint les modèles proposés
```

En mode passerelle, la passerelle est la source de vérité du catalogue : la portée de la clé
applicative (champ `models` de l'étape 2) fait office de liste blanche. Si `LLM_MODELS`
contient des entrées `gateway:`, elles restreignent en plus les modèles proposés. S'il n'en
contient aucune (liste vide, ou seulement des entrées d'autres fournisseurs comme
`ollama:llama3.1:8b` du `.env` de développement), tous les modèles servis sont proposés,
`LLM_MODEL_NAME` en tête s'il est servi. Les entrées d'autres fournisseurs sont ignorées :
un avertissement est journalisé une fois et affiché dans *Administration › Système*.

Redémarrez l'API. Le sélecteur de modèle du chat liste alors les modèles servis par la
passerelle ; un modèle dont le fournisseur n'a pas de clé reste proposé mais échouera. Les
embeddings ne passent pas par la passerelle (`EMBEDDING_PROVIDER` inchangé).

## 4. Définir un budget pour un utilisateur

Depuis ContextHub (ADR-002) : `PUT /admin/users/<uuid>/budget` avec
`{"limit": 1, "period": "month"}` (périodes `day`, `week`, `month` = 30 jours), `DELETE` pour
le retirer. ContextHub crée un budget dédié `contexthub-<uuid>` (`/budget/new`), le rattache
au client final (`/customer/new` ou `/customer/update`) et le supprime au retrait
(`/budget/delete`). Les appels ci-dessous restent utiles pour vérifier ou pour un budget
défini directement dans la passerelle (ContextHub ne modifie jamais ce dernier).

LiteLLM appelle « client final » (*customer*) l'utilisateur transmis par ContextHub : son
identifiant est l'UUID du compte ContextHub. Pour le connaître, connecté à ContextHub, ouvrez
http://localhost:5173/api/auth/me et relevez le champ `id`.

```bash
# Budget de 1 $ par période de 30 jours
curl -s http://127.0.0.1:4000/customer/new \
  -H "Authorization: Bearer sk-contexthub-dev-only-master-key" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<uuid-contexthub>", "max_budget": 1, "budget_duration": "30d"}'

# Si l'utilisateur est déjà connu de la passerelle (il a déjà posé une question) :
# attention, /customer/update modifie le budget auquel le client est rattaché, éventuellement
# partagé avec d'autres clients, et ignore "max_budget": null (un budget ne se retire pas ainsi).
curl -s http://127.0.0.1:4000/customer/update \
  -H "Authorization: Bearer sk-contexthub-dev-only-master-key" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<uuid-contexthub>", "max_budget": 1, "budget_duration": "30d"}'

# Vérifier : budget (litellm_budget_table) et dépense comptée
curl -s "http://127.0.0.1:4000/customer/info?end_user_id=<uuid-contexthub>" \
  -H "Authorization: Bearer sk-contexthub-dev-only-master-key"
```

Pour voir le refus « budget atteint », fixez un budget très bas (`"max_budget": 0.0001`) puis
posez une question avec un modèle payant (ou avec le prix fictif commenté dans `config.yaml`).

## 5. Vérifier de bout en bout

1. Posez une question dans le chat, avec un modèle de la passerelle. Le bandeau violet indique
   le modèle et la passerelle quand le modèle n'est pas local.
2. Attendez quelques dizaines de secondes : LiteLLM écrit les dépenses par lots
   (`proxy_batch_write_at`, 10 s) puis les agrège par jour.
3. Ouvrez **Ma consommation** (barre latérale, visible quand la passerelle est configurée) :
   une requête apparaît pour aujourd'hui, avec le modèle, les jetons et le coût (0 pour un
   modèle Ollama sans prix). Le budget de l'étape 4 s'affiche avec sa barre de progression.
4. En administrateur, *Administration › Consommation* montre la même ligne dans le tableau
   par utilisateur, sous le nom du compte ; l'export CSV s'ouvre dans Excel.
5. Avec un budget dépassé, la question suivante affiche dans le chat l'encart « Budget
   atteint » et un lien vers *Ma consommation* ; l'événement est inscrit au journal d'audit
   (`query.ask`, échec, raison `budget_exceeded`).

Côté passerelle, l'interface http://127.0.0.1:4000/ui (onglets *Usage* / *Logs*) montre les
mêmes requêtes, rattachées à l'UUID de l'utilisateur et aux étiquettes `app:contexthub` et
`step:condense|plan|generate`.

## Arrêter, réinitialiser

```bash
docker compose -f docker-compose.dev.yaml --profile gateway stop litellm litellm-db
# Repartir d'une passerelle vide (clés, budgets et dépenses effacés), sans toucher à la base de ContextHub :
docker compose -f docker-compose.dev.yaml --profile gateway rm -sf litellm litellm-db
docker volume ls --filter name=litellm_db_data      # puis : docker volume rm <nom affiché>
```

N'utilisez pas `down -v` pour cela : il effacerait aussi le volume PostgreSQL de ContextHub.

En production, la passerelle est un composant du client, déployé et sécurisé par lui (clés
propres, TLS, base sauvegardée) : voir `docs/deployment.md`, section « Passerelle LLM ».
