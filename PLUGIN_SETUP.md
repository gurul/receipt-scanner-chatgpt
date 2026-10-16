# Receipt Scanner plugin for ChatGPT mobile

This is a remote, authenticated MCP plugin. ChatGPT reads the receipt photo,
previews the extraction, and calls the server to save it. The server uses the
existing Python engine, persists purchases in SQLite, and returns costs and
expiring Excel download links. It needs no OpenAI API key: inference happens in
ChatGPT. There is no separate phone app to install.

The implementation and local integration tests are complete. It is **not yet
hosted, connected to an identity provider, installed in ChatGPT, or tested on a
physical phone**. Those steps require your deployment and account configuration.

## Run and test

The plugin server requires Python 3.12+ (the standalone engine still supports
Python 3.9). With `uv` installed:

```bash
uv sync --python 3.12 --extra test --locked
uv run --python 3.12 --extra test --locked pytest -q
```

Or create a Python 3.12 virtual environment and install `pip install -e '.[test]'`.
The tests use temporary databases and generated RSA keys; they don't need real
receipts or identity-provider credentials. They exercise the MCP HTTP protocol,
auth challenges, JWT rejection, scope enforcement, separate user ledgers,
concurrent duplicate recording, validation, revision conflicts, and exports.

## Configure authentication

Use an OAuth 2.1 authorization server that supports authorization code + S256
PKCE and ChatGPT client registration (CIMD, DCR, or a predefined client).
This project implements the **resource server**, not an identity provider.

Set up an API/resource with identifier **`https://YOUR_HOST/mcp`**, and enable
scope **`receipts:manage`**. The provider must issue **RS256-signed JWT access
tokens** with:

- `iss`: exactly `OAUTH_ISSUER`, including any trailing slash.
- `aud`: `https://YOUR_HOST/mcp`.
- `sub`: a stable, nonempty user identifier.
- `exp`: a short-lived expiry.
- `scope`: a space-separated string containing `receipts:manage`.

Configure discovery, S256 PKCE, refresh tokens, and the exact redirect URI shown
by ChatGPT when adding the MCP connection. The provider must honor the OAuth
`resource` parameter and bind the resulting access token to this API. Opaque
tokens, HS256 and ID tokens are not supported by this verifier. Keep the OAuth
provider's issuer/user IDs stable: those define account ownership.

The server publishes protected resource metadata at
`/.well-known/oauth-protected-resource/mcp`. Unauthenticated MCP requests return
a discovery challenge. Every tool requires sign-in and the scope above. All
ledger access is scoped to the verified issuer and subject; tool arguments never
select another user's account. See the official [OpenAI authentication guide](https://developers.openai.com/plugins/build/auth)
for the provider-side contract.

## Deploy

Copy `.env.example` to `.env` and fill in:

- `PUBLIC_URL`: your public HTTPS origin, without a path.
- `OAUTH_ISSUER`: exact issuer from your provider's discovery document.
- `OAUTH_JWKS_URL`: your provider's HTTPS signing-key endpoint.
- `DOWNLOAD_SECRET`: generate at least 32 random characters; do not use the example value.
- `DATABASE_PATH`: persistent database location (`/data/receipts.sqlite3` in Docker).

Generate a secret locally:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Run the server directly:

```bash
uv run --python 3.12 --locked --env-file .env receipt-scanner-server
```

Or deploy the provided image and persistent volume:

```bash
docker compose up --build -d
```

Compose binds port 8000 on localhost. Put an HTTPS reverse proxy in front, using
the same host as `PUBLIC_URL`, and route the entire origin to this service.
Do not rewrite `/mcp` or the discovery/download paths. For a temporary test,
an HTTPS tunnel can forward port 8000; set its origin in `PUBLIC_URL` and in the
OAuth resource configuration before starting the server.

Run **one server instance with one durable disk**. SQLite serializes writes to
prevent duplicate races. It is not a shared database for multiple replicas or
ephemeral/serverless disks. Back up the data volume using SQLite's backup API
or a consistent volume snapshot. Config and purchases survive process restarts.
The provider controls account registration and access policy.

`GET /health` is public and returns only service status. All MCP tools require
OAuth. Export URLs contain a signed five-minute capability so a mobile browser
can download a workbook without an OAuth header. They are account/export-bound,
not directory paths. Don't log query strings at the reverse proxy. Responses
use `Cache-Control: no-store`; expired export blobs are removed on subsequent
exports. The server stores extracted text and amounts, not uploaded photos.

## Connect and use on your phone

1. On ChatGPT web, open Plugins and add a custom MCP server at
   **`https://YOUR_HOST/mcp`**, using OAuth. Complete provider client/redirect
   configuration as prompted, create the plugin, install it, and sign in.
2. Open a new chat in the ChatGPT mobile app with the plugin available. Invoke
   Receipt Scanner and ask it to set up your business using your config JSON.
3. Attach a receipt photo and say **"Record this receipt."** Try a clear receipt
   first, then confirm totals, alias mapping, and reported costs.
4. Ask **"What are my costs and margins?"** and **"Export my purchases ledger."**
   The export link opens a downloadable workbook and expires after five minutes.

Availability depends on your ChatGPT account/workspace. Remote plugins can be
used on mobile; initial custom MCP setup is documented on the web. Official
references: [custom connection setup](https://developers.openai.com/api/docs/guides/custom-mcp-server),
[mobile plugin availability](https://learn.chatgpt.com/docs/plugins).

## Deployment source bundle

Create a deployment ZIP without needing a domain yet:

```bash
python3 scripts/package_deployment.py
```

This creates `dist/receipt-scanner-0.1.0-deployment.zip` and a SHA-256 checksum.
It includes the server, Docker setup, locked dependencies, examples, tests and
plugin packaging helper. It excludes credentials, databases, environments and
build caches. Unzip it and follow the authentication and deployment steps above.
This source bundle is for your host; generate the installable remote plugin
package after its HTTPS origin is known.

## Package the plugin

After you have a deployment origin, generate the portable package:

```bash
python3 scripts/package_plugin.py --url https://YOUR_HOST
```

This writes `dist/receipt-scanner.zip` with `plugin.json`, remote `mcp.json`, and
the receipt workflow skill. It contains no database, credentials, photos or local
runtime. The server is deployed separately. Personal MCP connections can be
tested before public distribution. For a public listing, submit the remote
endpoint and package through OpenAI's plugin review process; publication is not
performed by this repository. [Packaging reference](https://developers.openai.com/plugins/build/plugins).

## Tools and limits

| Tool | Behavior |
|---|---|
| `get_profile` | Stable identity for the current signed-in account |
| `get_business` | Current config and revision, or unconfigured status |
| `configure_business` | Replace config with a revision check; preserve purchases and remap aliases |
| `preview_receipt` | Validate, reconcile and preview mapping/costs without writing |
| `record_receipt` | Atomically append validated, nonduplicate receipt lines |
| `get_costs` | Latest prices, costs, missing data and tier margins |
| `list_purchases` | Paginated ledger, optionally only unmapped items |
| `update_alias` | Revision-checked alias update and historical remapping |
| `export_ledger` | Excel snapshot and five-minute download URL |

For config changes, read the current revision first. This prevents overwriting a
concurrent update. Each signed-in account owns one business; shared businesses,
team roles, receipt deletion/correction, ledger imports and Drive sync are not
implemented. A source workbook is not imported automatically when configuring.
Start this plugin's ledger with new receipts or explicitly plan a migration.

Unknown package contents block valid cost calculations. Negative return lines,
separate shipping/discount adjustments and tax allocation are not modeled by the
receipt schema; resolve these before recording. Identical same-day receipt
content is conservatively rejected even when separate transactions may be real.
OCR variation can evade content matching. Review extraction before recording.
