import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import time
from types import SimpleNamespace
import zipfile

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from openpyxl import load_workbook
import pytest

from receipt_plugin.auth import JWTVerifier, Settings, SCOPE
from receipt_plugin.models import BusinessConfig, Receipt, Alias
from receipt_plugin.server import create_server
from receipt_plugin.store import Store
from scripts.package_plugin import package

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path):
    return Settings(public_url="https://receipts.test", issuer="https://auth.test/",
                    jwks_url="https://auth.test/jwks", download_secret="s" * 48,
                    database_path=str(tmp_path / "receipts.sqlite3"))


@pytest.fixture
def config():
    return BusinessConfig.model_validate_json((ROOT / "example_business_config.json").read_text())


@pytest.fixture
def receipt():
    return Receipt.model_validate_json((ROOT / "example_receipt.json").read_text())


@pytest.fixture
def keys():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(settings, keys, **overrides):
    claims = {"iss": settings.issuer, "aud": settings.resource, "sub": "alice",
              "exp": int(time.time()) + 600, "scope": SCOPE}
    claims.update(overrides)
    return jwt.encode(claims, keys, algorithm="RS256", headers={"kid": "test"})


def verifier(settings, keys):
    result = JWTVerifier(settings)
    result.keys = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=keys.public_key()))
    return result


def test_persistence_isolation_preview_and_alias_remap(settings, config, receipt):
    store = Store(settings.database_path)
    assert store.get_business("alice")["configured"] is False
    store.configure("alice", config, 0)
    store.configure("bob", config, 0)
    preview = store.receipt("alice", receipt, commit=False)
    assert preview["recorded"] is False
    assert store.costs("alice")["ledger_rows"] == 0
    store.receipt("alice", receipt, commit=True)
    assert Store(settings.database_path).costs("alice")["ledger_rows"] == 5
    assert store.costs("bob")["ledger_rows"] == 0
    alias = Alias(patterns=["UNMATCHABLE"], category="Protein", default_unit="lb")
    result = store.aliases("alice", "Pork", alias, 1)
    assert result["revision"] == 2
    assert result["unmapped_count"] == 1
    assert result["product_costs"][0]["status"] == "incomplete"
    with pytest.raises(ValueError, match="Business changed"):
        store.configure("alice", config, 1)
    assert store.get_business("alice")["revision"] == 2


def test_concurrent_duplicates_commit_once(settings, config, receipt):
    store = Store(settings.database_path)
    store.configure("alice", config, 0)

    def record(_):
        try:
            store.receipt("alice", receipt, commit=True)
            return "recorded"
        except ValueError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(record, range(4)))
    assert results.count("recorded") == 1
    assert store.costs("alice")["ledger_rows"] == 5


def test_reconciliation_and_configuration_validation(config, receipt):
    bad = receipt.model_dump(mode="json")
    bad["subtotal"] = 57
    with pytest.raises(ValueError, match="Line totals"):
        Receipt.model_validate(bad)
    bad = config.engine_dict()
    bad["overhead"]["monthly_production"] = 0
    with pytest.raises(ValueError):
        BusinessConfig.model_validate(bad)
    bad = config.engine_dict()
    bad["sheets"]["tab_names"]["recipes"] = "Purchases"
    with pytest.raises(ValueError, match="unique"):
        BusinessConfig.model_validate(bad)


def test_portable_package_has_remote_endpoint_and_no_private_data(tmp_path):
    path = package("https://receipts.test", tmp_path / "plugin.zip")
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {"plugin.json", "mcp.json", "skills/receipt-scanner/SKILL.md"}
        config = json.loads(archive.read("mcp.json"))
        assert config["mcpServers"]["receipt-scanner"]["url"] == "https://receipts.test/mcp"
        assert json.loads(archive.read("plugin.json"))["name"] == "receipt-scanner"
    with pytest.raises(ValueError, match="HTTPS"):
        package("http://localhost", tmp_path / "bad.zip")


def test_export_is_account_scoped_and_text_cannot_become_formula(settings, config, receipt):
    store = Store(settings.database_path)
    store.configure("alice", config, 0)
    data = receipt.model_dump(mode="json")
    data["items"][0]["raw_description"] = '=HYPERLINK("https://example.com","GRD PORK")'
    store.receipt("alice", Receipt.model_validate(data), commit=True)
    export_id, _ = store.export("alice")
    assert store.download("bob", export_id) is None
    wb = load_workbook(BytesIO(store.download("alice", export_id)), data_only=False)
    cell = wb["Purchases"]["C2"]
    assert cell.value.startswith("=HYPERLINK")
    assert cell.data_type == "s"
    wb.close()
    expired_id, _ = store.export("alice", lifetime=-1)
    assert store.download("alice", expired_id) is None


def test_jwt_verification_rejects_invalid_credentials(settings, keys):
    async def run():
        check = verifier(settings, keys)
        assert (await check.verify_token(token(settings, keys))).subject == "alice"
        for changes in ({"iss": "https://evil.test"}, {"aud": "other-api"},
                        {"exp": int(time.time()) - 10}, {"sub": ""}):
            assert await check.verify_token(token(settings, keys, **changes)) is None
        assert await check.verify_token("garbage") is None
        assert await check.verify_token(token(settings, rsa.generate_private_key(public_exponent=65537, key_size=2048))) is None
    asyncio.run(run())


def test_remote_mcp_auth_workflow_and_mobile_download(settings, keys, config, receipt):
    async def run():
        server = create_server(settings, verifier(settings, keys))
        app = server.streamable_http_app()
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url=settings.public_url) as public:
                health = await public.get("/health")
                assert health.status_code == 200
                unauthorized = await public.post("/mcp", json={})
                assert unauthorized.status_code == 401
                assert "resource_metadata=" in unauthorized.headers["www-authenticate"]
                discovery = await public.get("/.well-known/oauth-protected-resource/mcp")
                assert discovery.json()["resource"] == settings.resource
                assert discovery.json()["authorization_servers"] == [settings.issuer]
                no_scope = await public.post("/mcp", json={}, headers={"Authorization": "Bearer " + token(settings, keys, scope="")})
                assert no_scope.status_code == 403

            headers = {"Authorization": "Bearer " + token(settings, keys)}
            async with httpx.AsyncClient(transport=transport, headers=headers) as client:
                async with streamable_http_client(settings.resource, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert len(tools.tools) == 9
                        assert all(t.model_dump()["securitySchemes"][0]["type"] == "oauth2" for t in tools.tools)
                        assert all(t.outputSchema for t in tools.tools)
                        initial = await session.call_tool("get_business", {})
                        assert initial.structuredContent["configured"] is False
                        configured = await session.call_tool("configure_business", {"config": config.engine_dict()})
                        assert not configured.isError
                        preview = await session.call_tool("preview_receipt", {"receipt": receipt.model_dump(mode="json")})
                        assert preview.structuredContent["recorded"] is False
                        saved = await session.call_tool("record_receipt", {"receipt": receipt.model_dump(mode="json")})
                        assert saved.structuredContent["ledger_rows"] == 5
                        duplicate = await session.call_tool("record_receipt", {"receipt": receipt.model_dump(mode="json")})
                        assert duplicate.isError
                        costs = await session.call_tool("get_costs", {})
                        assert costs.structuredContent["product_costs"][0]["total_cost_per_unit"] == 4.1125
                        export = await session.call_tool("export_ledger", {})
                        assert not export.isError
                        url = export.structuredContent["download_url"]
                        async with httpx.AsyncClient(transport=transport) as browser:
                            download = await browser.get(url)
                            assert download.status_code == 200
                            assert download.content[:2] == b"PK"
                            assert download.headers["cache-control"] == "no-store"
                            bad = await browser.get(url.replace("/downloads/", "/downloads/wrong"))
                            assert bad.status_code == 404
                            no_token = await browser.get(url.split("?")[0])
                            assert no_token.status_code == 404
                        invalid = receipt.model_dump(mode="json")
                        invalid["total"] = 999
                        rejected = await session.call_tool("record_receipt", {"receipt": invalid})
                        assert rejected.isError

            async with httpx.AsyncClient(transport=transport, headers={"Authorization": "Bearer " + token(settings, keys, sub="bob")}) as client:
                async with streamable_http_client(settings.resource, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        other = await session.call_tool("get_business", {})
                        assert other.structuredContent["configured"] is False
    asyncio.run(run())
