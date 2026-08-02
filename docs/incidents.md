# Incidents

## 2026-08-02 — Crash d'ingestion sur documents volumineux (Ollama / embeddings)

### Symptôme

Échec systématique de l'ingestion des PDF réglementaires volumineux (MiFID II —
`CELEX_32014L0065`, ~150 pages). Trois signatures d'erreur en cascade sur
`POST /api/embed` :

- `HTTP 400 Bad Request` renvoyé par Ollama,
- `ResponseError('Post "http://127.0.0.1:<port>/tokenize": dial tcp ... connectex:
  No connection could be made because the target machine actively refused it.')`,
- par intermittence `ConnectionError: Failed to connect to Ollama` et
  `RemoteProtocolError('Server disconnected without sending a response.')`.

Les 3 retries Celery échouaient tous, la tâche terminait en `FAILURE` et le
document passait en statut `failed`. Comportement déterministe : les petits
documents passaient, les gros échouaient toujours.

### Diagnostic

Le port interne aléatoire dans le message d'erreur est celui du *runner*
`bge-m3` qu'Ollama lance en sous-processus. « Connection refused » sur ce port
signifie que le runner était mort au moment où Ollama tentait de le joindre pour
tokeniser — l'erreur remontée au client n'était donc pas la cause mais sa
conséquence.

Cause racine : `load_source()` envoyait la totalité des chunks d'un document en
un seul appel `vector_store.add_documents(all_splits)`. `langchain_ollama`
transmet la liste complète à `embed_documents()`, soit **1362 chunks dans une
seule requête HTTP** pour MiFID II. Le runner chargeait l'ensemble en mémoire
pour la tokenisation et mourait avant de répondre — saturation mémoire du
sous-processus, mécanisme déduit de la corrélation stricte taille du
document ↔ échec, et confirmé a contrario par le correctif.

### Fausses pistes écartées

| Hypothèse | Pourquoi écartée |
|---|---|
| Modèle `bge-m3` absent | Présent dans `ollama list`, et les petits documents s'indexaient |
| Ollama non démarré | Le serveur répondait — c'est le *runner* du modèle qui mourait, pas le serveur |
| `OLLAMA_KEEP_ALIVE` mal réglé | Traite le déchargement du modèle *entre* documents, pas la saturation *intra*-document |
| Espacer les uploads manuellement | Le problème se produit dans le traitement d'un seul document |

### Solution

Batching des embeddings — envoi par lots de 32 chunks au lieu d'un appel
monolithique (`src/rag/rag_pipeline.py`, `BATCH_SIZE = 32`), avec log de
progression par lot.

Résultat : MiFID II indexé en 43 lots, 1362/1362 chunks, 70,8 s, aucune erreur.

### Enseignements d'architecture

- **Retry mal calibré.** `max_retries=3, countdown=5` rejouait à l'identique une
  requête vouée à échouer : ni backoff, ni réduction de charge. Un retry qui ne
  change aucun paramètre de la requête ne peut pas récupérer d'une erreur
  déterministe — il ne fait que retarder l'échec de 15 secondes.
- **L'ingestion naïve ne passe pas à l'échelle.** Tout pipeline d'embedding doit
  batcher *par conception*, pas en réaction à un crash. Le volume par document
  est une entrée non bornée.
- **Les erreurs de sous-processus mentent sur leur cause.** Un `connection
  refused` sur un port éphémère local désigne un processus mort, pas un problème
  réseau : il faut remonter à ce qui l'a tué.
- **Argument pour un service d'embeddings managé.** L'embedding local
  mono-machine est un point de fragilité sous charge. En production, un service
  managé (Vertex AI, Bedrock) absorbe ces volumes sans gestion manuelle de la
  mémoire.

### Améliorations futures

- `EMBED_BATCH_SIZE` configurable via variable d'environnement (aujourd'hui
  constante en dur).
- Backoff exponentiel sur les retries Celery, et réduction du batch size au
  retry plutôt que rejeu à l'identique.
- Métrique de débit d'embedding (chunks/s) exposée dans l'observabilité.
- Progression d'ingestion remontée au frontend via l'état de la tâche Celery
  (actuellement visible seulement dans les logs du worker).
