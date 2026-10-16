from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlparse
import os
import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .auth import Settings, JWTVerifier, account_id, SCOPE
from .models import BusinessConfig, Receipt, Alias, Text
from .store import Store


READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
REPLACE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


def create_server(settings: Settings, verifier=None):
    store = Store(settings.database_path)
    server = FastMCP(
        "Receipt Scanner", host=settings.host, port=settings.port, stateless_http=True,
        json_response=True,
        instructions="Read receipt photos, resolve unreadable fields and reconcile totals before recording. "
                     "Use get_business first; configure real business data if absent. Preview before recording. "
                     "Incomplete costs have no valid margin. Never bypass duplicate errors.",
        token_verifier=verifier or JWTVerifier(settings),
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.resource,
                          required_scopes=[SCOPE], validate_token_resource=True),
        transport_security=TransportSecuritySettings(
            allowed_hosts=[urlparse(settings.public_url).netloc, "127.0.0.1:*", "localhost:*"],
            allowed_origins=[settings.public_url.rstrip("/"), "https://chatgpt.com"]),
    )

    def identity():
        token = get_access_token()
        if token is None or SCOPE not in token.scopes:
            raise ValueError("Sign in to Receipt Scanner before accessing your ledger")
        return account_id(token)

    # The Python SDK exposes _meta; include the policy there and on Tool itself
    # so ChatGPT's per-tool authorization discovery sees the same contract.
    policy = [{"type": "oauth2", "scopes": [SCOPE]}]
    meta = {"securitySchemes": policy}

    @server.tool(annotations=READ, meta={**meta, "openai/profile": True})
    def get_profile() -> dict[str, Any]:
        """Return the stable profile ID for the signed-in Receipt Scanner account."""
        return {"id": identity()}

    @server.tool(annotations=READ, meta=meta)
    def get_business() -> dict[str, Any]:
        """Read your config and revision. If unconfigured, ask for actual business data; never use demo data silently."""
        return store.get_business(identity())

    @server.tool(annotations=REPLACE, meta=meta)
    def configure_business(config: BusinessConfig, expected_revision: Annotated[int, Field(ge=0)] = 0) -> dict[str, Any]:
        """Set aliases, recipes, overhead and pricing. Use revision 0 for first setup, otherwise get_business's revision.

        Replaces the config and remaps historical purchases, preserving the ledger. Use only changes the user requests.
        """
        return store.configure(identity(), config, expected_revision)

    @server.tool(annotations=READ, meta=meta)
    def preview_receipt(receipt: Receipt) -> dict[str, Any]:
        """Validate extracted receipt lines and preview mapping and cost changes without saving.

        Read the photo in ChatGPT first. Resolve unreadable fields, discounts or totals before submitting.
        Units must be printed or verified; never invent package sizes. Returns duplicate errors without writes.
        """
        return store.receipt(identity(), receipt, commit=False)

    @server.tool(annotations=WRITE, meta=meta)
    def record_receipt(receipt: Receipt) -> dict[str, Any]:
        """Save a receipt the user asked to record. Validate/preview extraction first.

        Persists atomically to the signed-in user's ledger. A duplicate errors without adding rows;
        do not edit IDs or data to evade duplicate protection. Returns before/after costs.
        """
        return store.receipt(identity(), receipt, commit=True)

    @server.tool(annotations=READ, meta=meta)
    def get_costs() -> dict[str, Any]:
        """Get latest prices, recipe costs and tier margins. Incomplete costs/margins are unavailable, never zero."""
        return store.costs(identity())

    @server.tool(annotations=READ, meta=meta)
    def list_purchases(limit: Annotated[int, Field(ge=1, le=100)] = 50,
                       offset: Annotated[int, Field(ge=0)] = 0, unmapped_only: bool = False) -> dict[str, Any]:
        """Read a page of ledger lines, optionally only unmapped purchases. Pagination uses row offsets."""
        return store.purchases(identity(), limit, offset, unmapped_only)

    @server.tool(annotations=REPLACE, meta=meta)
    def update_alias(canonical: Text, alias: Alias, expected_revision: Annotated[int, Field(ge=1)]) -> dict[str, Any]:
        """Set one user-approved alias and remap historical purchases. Read get_business for current revision first."""
        return store.aliases(identity(), canonical, alias, expected_revision)

    @server.tool(annotations=WRITE, meta=meta)
    def export_ledger() -> dict[str, Any]:
        """Create an Excel snapshot and a five-minute HTTPS download link usable on a phone.

        The URL grants access to this snapshot. Return it to the user as a download link.
        """
        account = identity()
        export_id, expires = store.export(account)
        token = jwt.encode({"sub": account, "jti": export_id, "exp": expires,
                            "aud": "receipt-export"}, settings.download_secret, algorithm="HS256")
        return {"download_url": settings.public_url.rstrip("/") + "/downloads/" + export_id + "?token=" + token,
                "filename": "purchases.xlsx", "expires_at": expires, "expires_in_seconds": 300}

    # FastMCP builds SDK Tool objects with extension fields supported by MCP.
    @server._mcp_server.list_tools()
    async def list_tools():
        tools = await server.list_tools()
        for tool in tools:
            tool.securitySchemes = policy
        return tools

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request):
        return JSONResponse({"status": "ok"})

    @server.custom_route("/downloads/{export_id}", methods=["GET"])
    async def download(request: Request):
        try:
            claims = jwt.decode(request.query_params.get("token", ""), settings.download_secret,
                                algorithms=["HS256"], audience="receipt-export",
                                options={"require": ["exp", "sub", "jti", "aud"]})
            if claims["jti"] != request.path_params["export_id"]:
                raise ValueError("Export ID mismatch")
            data = store.download(claims["sub"], claims["jti"])
            if data is None:
                raise ValueError("Export expired or missing")
        except (jwt.PyJWTError, ValueError, TypeError):
            return JSONResponse({"error": "Download unavailable or expired. Request a new export."}, status_code=404)
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="purchases.xlsx"',
                                 "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                 "X-Content-Type-Options": "nosniff"})

    return server


def create_app():
    return create_server(Settings.from_env()).streamable_http_app()


def main():
    import uvicorn
    # Download URLs contain temporary credentials, so don't log query strings.
    uvicorn.run(create_app(), host=os.getenv("HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "8000")), access_log=False)


if __name__ == "__main__":
    main()
