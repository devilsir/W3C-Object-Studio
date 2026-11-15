from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
import struct

TEXTURE_RE = re.compile(rb"([A-Za-z0-9_ ./\\@#$%+()\-]{1,250}\.(?:blp|tga|dds))", re.I)
MDL_IMAGE_RE = re.compile(r'\bImage\s+"([^"]+\.(?:blp|tga|dds))"', re.I)

@dataclass
class ModelInfo:
    path: Path
    format: str
    version: int | None
    textures: list[str]
    warnings: list[str]


def _clean_path(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="surrogateescape").strip().replace("/", "\\")


def scan_mdx(path: Path) -> ModelInfo:
    data = path.read_bytes()
    warnings: list[str] = []
    if not data.startswith(b"MDLX"):
        raise ValueError(f"{path.name}: cabeçalho MDLX ausente")
    version = None
    textures: list[str] = []
    pos = 4
    while pos + 8 <= len(data):
        tag = data[pos:pos+4]
        size = struct.unpack_from("<I", data, pos+4)[0]
        start = pos + 8
        end = start + size
        if end > len(data):
            warnings.append(f"chunk {tag!r} ultrapassa EOF; scan parcial")
            break
        chunk = data[start:end]
        if tag == b"VERS" and len(chunk) >= 4:
            version = struct.unpack_from("<I", chunk, 0)[0]
        elif tag == b"TEXS":
            if len(chunk) % 268 == 0:
                for i in range(0, len(chunk), 268):
                    ref = _clean_path(chunk[i+4:i+264])
                    if ref and ref not in textures:
                        textures.append(ref)
            else:
                warnings.append("TEXS não múltiplo de 268 bytes; usando scan de fallback")
        pos = end
    if not textures:
        for match in TEXTURE_RE.findall(data):
            ref = _clean_path(match)
            if ref and ref not in textures:
                textures.append(ref)
    if version is not None and version not in (800, 900, 1000, 1100):
        warnings.append(f"versão MDX incomum: {version}")
    return ModelInfo(path, "mdx", version, textures, warnings)


def scan_mdl(path: Path) -> ModelInfo:
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    version = None
    m = re.search(r"FormatVersion\s+(\d+)", text)
    if m:
        version = int(m.group(1))
    textures = []
    for ref in MDL_IMAGE_RE.findall(text):
        ref = ref.replace("/", "\\")
        if ref not in textures:
            textures.append(ref)
    return ModelInfo(path, "mdl", version, textures, [])


def scan_model(path: str | Path) -> ModelInfo:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".mdx":
        return scan_mdx(path)
    if ext == ".mdl":
        return scan_mdl(path)
    raise ValueError(f"modelo não suportado: {path}")


def match_textures(model: ModelInfo, texture_files: list[Path]) -> tuple[dict[str, Path], list[str]]:
    by_name: dict[str, list[Path]] = {}
    for p in texture_files:
        by_name.setdefault(p.name.casefold(), []).append(p)
    mapping: dict[str, Path] = {}
    missing: list[str] = []
    for ref in model.textures:
        name = Path(ref.replace("\\", "/")).name.casefold()
        choices = by_name.get(name, [])
        if len(choices) == 1:
            mapping[ref] = choices[0]
        else:
            missing.append(ref)
    return mapping, missing


def rewrite_texture_references(path: str | Path, replacements: dict[str, str]) -> int:
    """Rewrite texture references in a *temporary* MDX/MDL copy.

    Preview extraction sometimes has to rename an archive member because its
    MPQ byte-name is illegal as a Windows filename.  The Warcraft map is never
    touched; only the extracted preview copy is relinked to the safe local
    texture filenames.
    """
    path = Path(path)
    if not replacements:
        return 0
    ext = path.suffix.casefold()
    if ext == '.mdx':
        data = bytearray(path.read_bytes())
        if not data.startswith(b'MDLX'):
            return 0
        changed = 0
        pos = 4
        while pos + 8 <= len(data):
            tag = bytes(data[pos:pos + 4])
            size = struct.unpack_from('<I', data, pos + 4)[0]
            start = pos + 8
            end = start + size
            if end > len(data):
                break
            if tag == b'TEXS' and size % 268 == 0:
                for i in range(0, size, 268):
                    field_start = start + i + 4
                    field_end = field_start + 260
                    raw = bytes(data[field_start:field_end]).split(b'\0', 1)[0]
                    old = raw.decode('utf-8', errors='surrogateescape').replace('/', '\\')
                    new = replacements.get(old)
                    if not new:
                        continue
                    new_raw = str(new).replace('/', '\\').encode('utf-8', errors='surrogateescape')
                    if len(new_raw) > 259:
                        raise ValueError(f'texture preview path is too long for MDX TEXS: {new!r}')
                    data[field_start:field_end] = new_raw + b'\0' * (260 - len(new_raw))
                    changed += 1
            pos = end
        if changed:
            path.write_bytes(data)
        return changed

    if ext == '.mdl':
        text = path.read_text(encoding='utf-8', errors='surrogateescape')
        changed = 0
        pattern = re.compile(r'(\bImage\s+")([^"]+)(")', re.I)
        def repl(match):
            nonlocal changed
            old = match.group(2).replace('/', '\\')
            new = replacements.get(old)
            if not new:
                return match.group(0)
            changed += 1
            return match.group(1) + str(new).replace('/', '\\') + match.group(3)
        out = pattern.sub(repl, text)
        if changed:
            path.write_text(out, encoding='utf-8', errors='surrogateescape')
        return changed
    return 0
