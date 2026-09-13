"""Build a portable, allowlisted plugin ZIP without credentials or environment files."""
import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [root / name for name in [".mcp.json", ".gitignore", "pyproject.toml", "README.md", "README.en.md"]]
    for folder in [".codex-plugin", ".claude-plugin", "src/seedream_mcp", "tests", "docs", "examples", "scripts"]:
        files.extend(path for path in (root / folder).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts
                     and path.suffix in {".py", ".json", ".toml", ".md"})
    # Include only the three known logo assets, never arbitrary user media.
    files.extend(root / name for name in [
        "assets/logo.png", "assets/icon.png", "src/seedream_mcp/assets/icon.png"])
    # Explicit workflow allowlist: no runtime manifests, URLs, media or model weights.
    skill = root / "skills/reference-video-production"
    files.extend(skill / name for name in [
        "SKILL.md", "agents/openai.yaml", "references/plugin-contracts.md",
        "references/segmentation.md", "references/prompt-contract.md"])
    # Exclusive creation: never overwrite an existing delivery.
    with ZipFile(output, "x", ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, path.relative_to(root).as_posix())
    print(f"Packaged {len(set(files))} files: {output}")


if __name__ == "__main__":
    main()
