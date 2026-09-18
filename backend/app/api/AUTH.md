# Application Authentication

The application uses one deployment-specific fixed secret. Browsers submit its
SHA-256 digest once to obtain a permanent opaque bearer token. There are no user
accounts or roles. Existing seller epochs, GUI confirmations, and outbox guards
still apply after authentication.

## Provisioning

Set `MAA_AUTH_SECRET_SHA256` in the deployment's `.env` or process environment
before starting `python -m backend.app.main`. It must contain exactly 64 hex
characters. The digest is SHA-256 of the exact UTF-8 secret: spaces are significant
and no Unicode normalization is performed. Missing or invalid configuration
prevents startup before service spawning. There is no default secret or bypass.

For an existing secret, compute its digest without putting the secret in shell
history (run from the repository root):

```powershell
python -c "import getpass,hashlib; print(hashlib.sha256(getpass.getpass('Fixed secret: ').encode('utf-8')).hexdigest())"
```

Treat the resulting digest as a credential too. Configure a high-entropy secret
per installation, and keep `.env` private. Never place the digest in
`NEXT_PUBLIC_*`, frontend assets, URLs, logs, or shared example configuration.
Packaged installations must provision this setting as well; putting one secret
in a shared CI `DOTENV` distributes that same credential to every installer.

The default session database is `backend/data/auth.sqlite`. Optional
`MAA_AUTH_DB_PATH` selects another file; an absolute path is recommended, and a
relative override resolves against the process working directory. The database
is independent of seller configuration and CRM. Keep its directory private and
persistent across backend restarts. Only token hashes and a double-hashed secret
fingerprint are stored. Storage failures fail closed.

## HTTP Contract

All responses use the existing `{code, msg, data}` envelope and actual HTTP error
statuses. Authentication and protected API responses have `Cache-Control:
no-store`. Credentials are never accepted in query parameters or cookies.

| Endpoint | Input | Success data |
| --- | --- | --- |
| `POST /api/auth/login` | JSON `{"secret_sha256":"<64 hex characters>"}` | `{"token":"maa_<opaque random value>"}` |
| `GET /api/auth/session` | `Authorization: Bearer <token>` | `{"authenticated":true}` |
| `POST /api/auth/logout` | `Authorization: Bearer <token>` | `null` |

Only the exact login method and path accept the fixed-secret digest. All `/api`
and `/api/*` requests otherwise require an issued token, including settings,
status, model keys, export, screenshots, and shutdown. Unknown API routes and
ordinary OPTIONS requests are protected too. Genuine CORS preflights are handled
before authentication, with the existing allowed development origins. Frontend
HTML/assets remain public; `/docs`, `/redoc`, and `/openapi.json` are disabled.

Invalid or missing tokens produce 401, login validation errors produce 422,
incorrect secrets produce 401, and unavailable authentication storage produces
503. API clients must retain the returned token and still send account epochs
where the business contract requires them.

## Login Rate Limit

One global bucket admits at most one login attempt every two seconds (0.5 RPS,
capacity one), before request-body parsing. Correct, incorrect, and malformed
attempts consume the same budget, regardless of source IP or forwarded headers.
Excess attempts immediately return 429 with integer `Retry-After` seconds.
Rejected attempts do not extend the cooldown. Clients should honor the header;
there is no server-side queue or automatic frontend login retry loop.

Session validation, logout, normal API operations, and internal MITM traffic do
not consume login capacity. Business endpoints never compare the fixed-secret
digest, so they cannot serve as an alternate login-guessing endpoint.

The supported deployment is one API process. The limiter is atomic within that
process and resets on restart. Do not run multiple workers or replicas expecting
an installation-wide 0.5 RPS limit; that requires shared atomic admission storage
and changes to the application's other process-local services. A noisy caller
can occupy this deliberately global login budget.

## Permanent Sessions

Tokens contain 256 random bits and have no expiration time. Restarting the
browser or backend does not invalidate them. The browser persists only the opaque
token under `maa:auth:v1`, and validates it before mounting the account provider
or starting business polling. Logout removes only authentication storage, leaving
drafts and outbox recovery records intact.

Cross-tab writes and removals use a shared Web Lock. Without usable localStorage
or Web Locks, the login screen offers an explicit temporary session. Network
errors preserve existing credentials; a current-session 401 clears the session.
Stale requests cannot invalidate a newer login. If server-side logout fails, the
browser stops local requests and warns that the credential may remain valid.

Changing `MAA_AUTH_SECRET_SHA256` and restarting revokes all existing sessions in
one database transaction. Changing back to an older secret does not restore
previously revoked tokens. To revoke every session without changing the secret,
run locally with the same configuration and database as the application:

```powershell
python -m backend.app.api.auth_cli revoke-all
```

Deleting site storage alone does not revoke a server-side token. Backups of the
authentication database can contain valid session hashes; restoring an old
backup can restore its session state. Use the revocation command after restoring
an authentication backup if those sessions should no longer be trusted.

## MITM Ingestion

The separate Python receiver accepts only `POST /internal/traffic` with its
internal Bearer credential, checking it before reading the body. It binds only
loopback addresses and retains the 10 MiB request-body limit. Browser session
tokens cannot authenticate ingestion, and internal credentials cannot access the
business API.

The main launcher generates a fresh internal credential on each start and passes
it to the receiver and Yak's child environment as `MAA_MITM_INTERNAL_TOKEN`.
Normal deployments should not configure this variable themselves. Yak uses the
configured receiver host/port, reports non-2xx responses, and does not follow
redirects when forwarding credentials.

For standalone receiver/Yak operation, both processes must receive the same
private internal token in their process environment. The receiver's standalone
entry point does not load `.env`. Use a high-entropy ASCII bearer token distinct
from browser tokens (which start with `maa_`). Missing credentials stop startup.

## Transport and Browser Boundary

SHA-256 is not transport encryption: the login digest can be replayed, and anyone
holding a token can use it until revocation. Use HTTPS for remote access. Browser
hashing and Web Locks require a secure context; localhost HTTP is supported,
ordinary LAN HTTP generally is not.

localStorage is intentionally used for permanent browser storage and is readable
by same-origin scripts. Preserve the application's XSS defenses and do not load
untrusted scripts into this origin. The frontend sends explicit authorization
headers with cookies omitted. Keep the existing restricted CORS policy.
