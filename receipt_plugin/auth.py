"""OAuth resource server: issuer, audience, signature, expiry and scopes checked."""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlparse
import anyio
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken
from pydantic import BaseModel, Field, model_validator

SCOPE = "receipts:manage"


class Settings(BaseModel):
    public_url: str
    issuer: str
    jwks_url: str
    download_secret: str = Field(min_length=32)
    database_path: str = "data/receipts.sqlite3"
    host: str = "127.0.0.1"
    port: int = 8000

    @model_validator(mode="after")
    def validate_urls(self):
        if self.download_secret.startswith("replace-"):
            raise ValueError("Generate a random DOWNLOAD_SECRET; do not use the example value")
        for value in (self.public_url, self.issuer, self.jwks_url):
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Public, issuer and JWKS URLs must be HTTPS URLs without credentials/fragments")
        if urlparse(self.public_url).path not in ("", "/") or urlparse(self.public_url).query:
            raise ValueError("PUBLIC_URL must be an HTTPS origin without a path or query")
        return self

    @property
    def resource(self):
        return self.public_url.rstrip("/") + "/mcp"

    @classmethod
    def from_env(cls):
        return cls(public_url=os.environ["PUBLIC_URL"], issuer=os.environ["OAUTH_ISSUER"],
                   jwks_url=os.environ["OAUTH_JWKS_URL"], download_secret=os.environ["DOWNLOAD_SECRET"],
                   database_path=os.getenv("DATABASE_PATH", "data/receipts.sqlite3"),
                   host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))


class JWTVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.keys = PyJWKClient(settings.jwks_url, cache_keys=True, timeout=5)

    async def verify_token(self, token: str) -> AccessToken | None:
        def decode():
            key = self.keys.get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=["RS256"], issuer=self.settings.issuer,
                              audience=self.settings.resource, options={"require": ["exp", "iss", "aud", "sub"]})
        try:
            claims = await anyio.to_thread.run_sync(decode)
            subject = claims["sub"]
            if not isinstance(subject, str) or not subject.strip():
                return None
            scope = claims.get("scope", "")
            if not isinstance(scope, str):
                return None
            return AccessToken(token=token, client_id=str(claims.get("client_id", claims.get("azp", "chatgpt"))),
                               scopes=scope.split(), expires_at=int(claims["exp"]),
                               resource=self.settings.resource, subject=subject, claims=claims)
        except (jwt.PyJWTError, ValueError, TypeError, OSError):
            return None


def account_id(token: AccessToken) -> str:
    if not token.subject or not token.claims or not token.claims.get("iss"):
        raise ValueError("Authenticated identity is unavailable")
    return hashlib.sha256((token.claims["iss"] + "\0" + token.subject).encode()).hexdigest()
