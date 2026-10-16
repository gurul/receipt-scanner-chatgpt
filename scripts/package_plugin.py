"""Build the portable plugin ZIP with the deployed remote endpoint."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package(public_url: str, output: Path):
    parsed = urlparse(public_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
            parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Use your public HTTPS origin, without a path, query, fragment or credentials")
    manifest = json.loads((ROOT / "plugin.json").read_text())
    mcp = {"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
           "mcpServers": {"receipt-scanner": {"type": "streamable-http", "url": public_url.rstrip("/") + "/mcp"}}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps(manifest, indent=2) + "\n")
        archive.writestr("mcp.json", json.dumps(mcp, indent=2) + "\n")
        for path in sorted((ROOT / "skills").rglob("*.md")):
            archive.write(path, path.relative_to(ROOT))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Deployed HTTPS origin, e.g. https://receipts.yourdomain.com")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "receipt-scanner.zip")
    args = parser.parse_args()
    try:
        print(package(args.url, args.out))
    except ValueError as error:
        parser.error(str(error))
