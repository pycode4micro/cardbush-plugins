"""Build a source distribution with an explicit allowlist. No credentials or caches."""

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def bundle(root: Path, destination: Path) -> tuple[int, str]:
    root = root.resolve()
    fixed = {
        "plugin.json",
        "mcp.json",
        ".mcp.json",
        ".codex-plugin/plugin.json",
        "pyproject.toml",
        "uv.lock",
        ".gitignore",
        ".env.example",
        "README.md",
    }
    files = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts) and rel.as_posix() not in fixed:
            continue
        allowed = rel.as_posix() in fixed or (
            rel.parts[0] in {"src", "tests", "scripts", "skills", "docs"}
            and "__pycache__" not in rel.parts
            and path.suffix in {".py", ".md", ".json"}
        )
        if allowed:
            files.append(path)
    for required in fixed:
        if not (root / required).is_file():
            raise FileNotFoundError(f"Required package file missing: {required}")
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, (Path(root.name) / path.relative_to(root)).as_posix())
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{digest}  {destination.name}\n", encoding="ascii"
    )
    return len(files), digest


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    version = json.loads((root / "plugin.json").read_text(encoding="utf-8"))["version"]
    destination = root.parent / f"{root.name}-{version}.zip"
    count, digest = bundle(root, destination)
    print(json.dumps({"archive": str(destination), "files": count, "sha256": digest}, indent=2))


if __name__ == "__main__":
    main()
