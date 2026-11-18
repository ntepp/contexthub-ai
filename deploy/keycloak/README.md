# Local Keycloak for SSO development

A throw-away identity provider to try the OpenID Connect login and the SSO group
grants. **Development only**: fixed passwords, fixed client secret, no TLS, in-memory
database (every restart re-imports `contexthub-realm.json`).

## Start

```bash
docker compose -f docker-compose.dev.yaml --profile sso up -d
```

Then in `.env`:

```
OIDC_ISSUER=http://127.0.0.1:8081/realms/contexthub
OIDC_CLIENT_ID=contexthub
OIDC_CLIENT_SECRET=contexthub-dev-secret
OIDC_LABEL=Keycloak
OIDC_ADMIN_GROUPS=contexthub-admins
```

Restart the API. The login page shows "Se connecter avec Keycloak".

## Demo accounts (realm `contexthub`)

| User | Password | Group | In ContextHub |
|---|---|---|---|
| `alice` | `alice-demo-2026` | `rh` | member; sees the collections granted to `rh` |
| `chloe` | `chloe-demo-2026` | `juridique` | member; sees the collections granted to `juridique` |
| `bob` | `bob-demo-2026` | `contexthub-admins` | administrator (through `OIDC_ADMIN_GROUPS`) |

Keycloak admin console: http://127.0.0.1:8081 (`admin` / `admin`).

## Group grants

In ContextHub, *Administration › Accès*, choose a collection and give access to a group
(`rh`, `juridique`…). Members get the collection at their next SSO login; the groups are
read again at every login, so leaving a group in the directory removes the access.

The realm sends the groups in the `groups` claim of the ID token (mapper
`oidc-group-membership-mapper`, short names). With another identity provider, set
`OIDC_GROUPS_CLAIM` to the claim that carries them (Entra ID sends group object ids).

## Why 127.0.0.1

The issuer written in the tokens must be the address both the browser and the API use.
On Windows, `localhost` can resolve to `::1` first and stall server-side calls, so the
realm is published as `http://127.0.0.1:8081` (`KC_HOSTNAME`).
