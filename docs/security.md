# Sécurité et flux de données

Document destiné aux RSSI, DSI et DPO qui évaluent ContextHub avant un déploiement.

## 1. Modèle de déploiement

Une instance par client, sur l'infrastructure du client (serveur, VM ou cluster). Aucun
composant n'est hébergé par l'éditeur ; aucune télémétrie n'est envoyée à l'éditeur.

```
 Navigateur ──HTTPS──▶ nginx (web) ──▶ API FastAPI ──▶ PostgreSQL + pgvector
                         │                │       └──▶ Redis (file d'attente, limites de débit)
                         │                └──▶ Worker Celery (indexation) ──▶ disque /data/uploads
                         │                └──▶ Ollama (LLM + embeddings locaux)          [optionnel]
                         └ seul service exposé            └──▶ fournisseur LLM cloud     [optionnel, opt-in]
```

Seul le service `web` publie un port. PostgreSQL, Redis, l'API, le worker et Ollama ne sont
joignables que sur le réseau Docker interne.

## 2. Flux de données sortants

| Configuration | Ce qui sort de l'infrastructure |
|---|---|
| `LLM_MODELS` ne contient que des modèles `ollama:` et `EMBEDDING_PROVIDER=ollama` | **Rien.** Mode 100 % local. |
| Un modèle `anthropic:`, `openai:` ou `openai_compatible:` (non local) est autorisé | Quand un utilisateur le choisit : la question et les **extraits de documents retenus** (8 au plus) sont envoyés au fournisseur. L'interface l'indique en permanence. |
| `EMBEDDING_PROVIDER=openai` | Le texte intégral des documents est envoyé au fournisseur lors de l'indexation. Déconseillé. |
| `LANGCHAIN_TRACING_V2=true` | Prompts et extraits envoyés à LangSmith. **Désactivé par défaut**, forcé à `false` au démarrage sinon. |
| `ALLOW_URL_INGESTION=true` | Le serveur télécharge les pages web demandées par les éditeurs. Désactivé par défaut. |

La page *Administration › Système* affiche le mode effectif (« 100 % local » ou la liste des
fournisseurs cloud autorisés).

## 3. Authentification

- **Comptes locaux** : mots de passe hachés avec bcrypt (coût 12) ; 12 caractères minimum et trois
  classes de caractères ; limitation à 10 tentatives par couple adresse IP + compte sur 15 minutes (configurable).
- **SSO OpenID Connect** (Entra ID, Google Workspace, Okta, Keycloak…) : flux *Authorization
  Code* avec PKCE, vérification de la signature de l'`id_token` via JWKS (algorithmes
  asymétriques uniquement), de l'émetteur, de l'audience et du nonce ; restriction possible par
  domaine de messagerie ; rôle administrateur dérivé des groupes (`OIDC_ADMIN_GROUPS`). La
  connexion par mot de passe peut être désactivée (`LOCAL_LOGIN_ENABLED=false`).
- **Session** : jeton signé (HS256, `SECRET_KEY`) dans un cookie `HttpOnly`, `Secure` et
  `SameSite=Strict`, durée configurable (8 h par défaut). Changer de mot de passe, être
  désactivé ou changer de rôle révoque immédiatement toutes les sessions.
- **CSRF** : en plus de `SameSite=Strict`, toute requête modifiante authentifiée par cookie doit
  porter l'en-tête `X-Requested-With`, qu'un formulaire tiers ne peut pas poser et qu'un script
  d'une autre origine ne peut pas envoyer sans pré-vérification CORS (refusée).
- **Clés d'API** personnelles pour les intégrations : seule leur empreinte SHA-256 est stockée ;
  révocables ; elles agissent avec les droits de leur propriétaire.

## 4. Autorisations

- Rôles globaux : `admin` (tous les droits) et `member`.
- Droits par collection : `viewer` (interroger) et `editor` (importer, supprimer des documents).
  Une collection peut être « ouverte » (lecture pour tous les membres).
- Une collection non autorisée répond 404 : son existence n'est pas divulguée.
- Le périmètre de recherche est filtré **dans la requête SQL** : un passage d'une collection non
  autorisée ne peut pas entrer dans le contexte du LLM.
- Garde-fous : impossible de rétrograder, désactiver ou supprimer le dernier administrateur actif.

## 5. Journal d'audit

Chaque action est journalisée : connexions (réussies, échouées, bloquées), questions et
réponses (texte intégral, sources citées, modèle, latence), consultations et téléchargements de
documents, imports, suppressions, gestion des comptes et des droits, exports du journal.

- **Ajout seul et chaînage** : chaque événement contient `hash = SHA-256(hash précédent ‖
  événement)`. Toute modification, insertion ou suppression d'une ligne en base est détectée par
  *Vérifier l'intégrité* (ou `python -m src.cli verify-audit`, code retour 2 si rompu).
- Les données personnelles de l'acteur (adresse e-mail, IP, navigateur) sont **hors du calcul
  d'empreinte** : elles peuvent être effacées (RGPD) sans rompre la chaîne.
- Export CSV (UTF-8, séparateur `;`) filtrable par période et type d'action ; l'export est
  lui-même journalisé.
- Durée de conservation configurable (`AUDIT_RETENTION_DAYS`, 5 ans dans le modèle de
  production) ; la purge est journalisée et la vérification repart du premier événement restant.

Limite : le chaînage détecte une altération, il ne l'empêche pas. Pour une garantie d'opposabilité,
exportez régulièrement le journal vers un stockage WORM ou un SIEM.

## 6. Documents

- Formats acceptés : PDF, DOCX, TXT, Markdown, HTML. Le type réel est vérifié sur le contenu
  (signature binaire), pas seulement sur l'extension.
- Taille maximale configurable (`UPLOAD_MAX_MB`, 50 Mo), appliquée aussi par nginx.
- Le nom fourni par le client n'est jamais utilisé sur le disque : fichiers stockés sous
  `<collection>/<identifiant><extension>`, lecture uniquement sous le répertoire d'upload.
- Les HTML sont réduits à leur texte (scripts, styles et formulaires supprimés).
- Les fichiers originaux sont servis avec `Cache-Control: no-store` et `nosniff`, après contrôle
  des droits, et chaque consultation est journalisée.
- Import d'URL (désactivé par défaut) : http(s) sur ports 80/443 uniquement, résolution DNS
  vérifiée (adresses privées, de bouclage, lien-local, réservées et métadonnées cloud refusées),
  chaque redirection revérifiée, taille plafonnée. Risque résiduel : rebinding DNS entre la
  vérification et la connexion.

## 7. Protection des données (RGPD)

| Besoin | Mise en œuvre |
|---|---|
| Effacement d'un utilisateur | *Administration › Utilisateurs › Supprimer* : compte, conversations, clés d'API et droits supprimés ; e-mail, IP et navigateur masqués dans le journal |
| Effacement d'un document | Fichier original, passages indexés et métadonnées supprimés |
| Effacement d'une collection | Tous ses documents, fichiers, passages et droits |
| Durées de conservation | `AUDIT_RETENTION_DAYS`, `CONVERSATION_RETENTION_DAYS`, purge quotidienne automatique |
| Minimisation | Aucune donnée envoyée hors de l'infrastructure en mode local |

Les questions des utilisateurs restent dans le journal d'audit après suppression du compte, sans
lien vers une identité : c'est un enregistrement d'activité de l'organisation. Si votre analyse
d'impact l'exige, réduisez `AUDIT_RETENTION_DAYS`.

## 8. Durcissement de la plateforme

- L'API refuse de démarrer en production avec une `SECRET_KEY` absente ou courte, ou des mots de
  passe PostgreSQL, Redis ou administrateur laissés à leur valeur d'exemple.
- Erreurs internes renvoyées sans détail technique, avec un identifiant de corrélation
  (`X-Request-ID`) retrouvable dans les journaux.
- En-têtes de sécurité : CSP stricte (aucune ressource externe, polices auto-hébergées),
  `X-Frame-Options`, `nosniff`, `Referrer-Policy`, HSTS en TLS.
- Swagger désactivé en production.
- Conteneurs applicatifs exécutés en utilisateur non privilégié.
- Redis protégé par mot de passe ; aucun port de base de données publié.
- Dépendances Python épinglées ; tests automatisés (y compris contrôle d'accès) en CI.

## 9. Recommandations d'exploitation

1. Terminer le TLS (nginx fourni avec `NGINX_CONF=tls`, ou reverse-proxy de l'entreprise).
2. Activer le SSO et désactiver la connexion par mot de passe une fois le SSO validé.
3. Planifier `scripts/backup.sh` et copier les sauvegardes hors de l'hôte.
4. Exporter le journal d'audit mensuellement vers votre SIEM ou un stockage immuable.
5. Restreindre `LLM_MODELS` aux fournisseurs validés par votre politique de sécurité.
6. Suivre `/api/ready` (supervision) et `/metrics` en interne (Prometheus).

## 10. Signaler une vulnérabilité

Écrivez à ntepp.marcus@gmail.com ; n'ouvrez pas d'issue publique.
Réponse sous 5 jours ouvrés ; correctif prioritaire pour les clients sous licence commerciale.
