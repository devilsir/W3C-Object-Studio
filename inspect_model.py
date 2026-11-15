from __future__ import annotations
import argparse
import json
from pathlib import Path

from wc3injector.modelscan import scan_model, match_textures


def main():
    ap = argparse.ArgumentParser(description="Inspect MDX/MDL texture references")
    ap.add_argument("model", type=Path)
    ap.add_argument("--texture", type=Path, action="append", default=[])
    args = ap.parse_args()

    info = scan_model(args.model)
    mapping, missing = match_textures(info, args.texture)
    print(json.dumps({
        "model": str(info.path),
        "format": info.format,
        "version": info.version,
        "texture_references": info.textures,
        "matched": {k: str(v) for k, v in mapping.items()},
        "missing_or_ambiguous": missing,
        "warnings": info.warnings,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
