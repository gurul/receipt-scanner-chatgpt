"""Package deployable source, examples and checks without private/local files."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    "README.md", "PLUGIN_SETUP.md", "Dockerfile", "compose.yaml", ".dockerignore",
    ".env.example", ".gitignore", "pyproject.toml", "uv.lock", "requirements-plugin.lock",
    "requirements.txt", "plugin.json", "cost_engine.py", "instructions.md",
    "example_business_config.json", "example_receipt.json",
)


def package():
    version = json.loads((ROOT / "plugin.json").read_text())["version"]
    prefix = f"receipt-scanner-{version}"
    output = ROOT / "dist" / f"{prefix}-deployment.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = [ROOT / name for name in ROOT_FILES]
    paths += sorted((ROOT / "receipt_plugin").glob("*.py"))
    paths += sorted((ROOT / "scripts").glob("*.py"))
    paths += sorted((ROOT / "tests").glob("test_*.py"))
    paths += sorted((ROOT / "skills").glob("*/SKILL.md"))
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, f"{prefix}/{path.relative_to(ROOT).as_posix()}")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n")
    return output


if __name__ == "__main__":
    print(package())
