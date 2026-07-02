"""RDR1 (RDR PC) rgxd .fxc shader-container support.

RDR1's .fxc archives are NOT the DX11 GTA5 .fxc format (see core/fxc_parser.py)
nor the DX12 GTA5 .awc format. They are RAGE 'rgxd' containers that embed
SM6.0 DXIL (DXBC-wrapped) blobs back to back, each preceded by a small record
prelude (shader name + parameter/sampler/texture name table) and a little-
endian u16 bytecode-size field immediately before the DXBC magic.

This module scans, extracts and rebuilds those containers. It is a library
port of the standalone rdr_fxc_tool.py, exposing unpack()/repack() for the GUI.

Key facts established while reverse-engineering RDR1 (do not regress these):
  * Blobs are SM6.0 DXIL, target profile ps_6_0 / vs_6_0 (NOT 5_x). The DX12
    compile path (dxc, no -flegacy-resource-reservation) handles them.
  * Replacing a blob may change its size; every later blob shifts. Rebuild
    patches the u16 record size and splices correctly -> this is safe and was
    verified byte-identical on an identity repack.
  * The shader's FNV-1 hash changing does NOT break rendering (psocache
    tolerates it) -- no hash forging is needed.
  * The REAL gotcha is the decompiler mangling I/O signatures; that is handled
    separately by core/semantic_fixup.py during decompile, not here.
"""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass

DXBC_MAGIC = b"DXBC"
NAME_RE = re.compile(rb"\b(?:VS|PS|CS|GS|HS|DS)[A-Za-z0-9_]{2,}\b")
# A shader entry name starts with a stage tag (vs/ps/cs/gs/hs/ds, either case)
# followed by '_' or a letter -- this matches 'vs_MiniSky', 'ps_main',
# 'VSTerrain', 'PSTerrain_Flatten' while excluding the 'g...'/'MoonSpace...'
# parameter names and 'NULL' placeholders that also live in the record.
SHADER_NAME_RE = re.compile(r"^(?:vs|ps|cs|gs|hs|ds)(?:_|[A-Za-z])", re.IGNORECASE)
MANIFEST = "rdr1_manifest.json"


@dataclass
class Blob:
    index: int
    name: str
    offset: int
    size: int
    record_size_offset: int | None


def _dxbc_size(data: bytes, offset: int) -> int:
    if data[offset:offset + 4] != DXBC_MAGIC:
        raise ValueError(f"no DXBC magic at 0x{offset:X}")
    if offset + 0x20 > len(data):
        raise ValueError(f"truncated DXBC header at 0x{offset:X}")
    size = int.from_bytes(data[offset + 0x18:offset + 0x1C], "little")
    chunks = int.from_bytes(data[offset + 0x1C:offset + 0x20], "little")
    if size <= 0 or offset + size > len(data):
        raise ValueError(f"bad DXBC size 0x{size:X} at 0x{offset:X}")
    if chunks <= 0 or chunks > 64:
        raise ValueError(f"bad DXBC chunk count {chunks} at 0x{offset:X}")
    return size


def _pascal_strings(prelude: bytes) -> list[str]:
    """Walk the record's length-prefixed pascal strings (length byte counts the
    trailing NUL). Records are a run of such strings (shader name, then the
    parameter-name table); a leading 'rgxd' file header on the first record and
    stray non-string bytes are skipped by advancing one byte on a bad parse."""
    out = []
    p = 10 if prelude[:4] == b"rgxd" else 0
    while p < len(prelude):
        length = prelude[p]
        if 2 < length <= 64 and p + 1 + length <= len(prelude):
            cand = prelude[p + 1:p + 1 + length].rstrip(b"\x00")
            if cand and all(32 <= c < 127 for c in cand):
                out.append(cand.decode("ascii"))
                p += 1 + length
                continue
        p += 1
    return out


def _infer_name(prelude: bytes, index: int) -> str:
    strings = _pascal_strings(prelude)
    # Preferred: the first pascal string that looks like a shader entry name
    # (stage-prefixed), which skips 'NULL' placeholders and 'g*' param names.
    for s in strings:
        if SHADER_NAME_RE.match(s):
            return s
    # Fallback: a stage-prefixed token anywhere in the prelude.
    matches = NAME_RE.findall(prelude)
    if matches:
        return matches[-1].decode("ascii", errors="replace")
    # Last resort: the first printable pascal string, else a blob placeholder.
    for s in strings:
        if s != "NULL" and not s.startswith("g"):
            return s
    return f"blob_{index:02d}"


def scan(data: bytes) -> list[Blob]:
    """Locate every DXBC blob and its preceding u16 record-size field."""
    offsets: list[tuple[int, int]] = []
    pos = 0
    while True:
        off = data.find(DXBC_MAGIC, pos)
        if off < 0:
            break
        try:
            size = _dxbc_size(data, off)
        except ValueError:
            pos = off + 4
            continue
        offsets.append((off, size))
        pos = off + 4

    blobs: list[Blob] = []
    prev_end = 0
    for index, (off, size) in enumerate(offsets):
        prelude = data[prev_end:off]
        record_size_offset = None
        if off >= 2 and int.from_bytes(data[off - 2:off], "little") == size:
            record_size_offset = off - 2
        blobs.append(Blob(index, _infer_name(prelude, index), off, size, record_size_offset))
        prev_end = off + size
    return blobs


def is_rdr1_fxc(path: str) -> bool:
    """Cheap sniff: an rgxd .fxc has >=1 embedded DXBC blob carrying a DXIL
    chunk (SM6). Distinguishes RDR1 containers from GTA5 DX11 .fxc."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    blobs = scan(data)
    if not blobs:
        return False
    b = blobs[0]
    return b"DXIL" in data[b.offset:b.offset + b.size]


def safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "_-") or "shader"


def unpack(fxc_path: str, out_dir: str, ext: str = ".cso") -> list[dict]:
    """Extract each blob as NN_Name<ext> into out_dir and write a manifest.
    Returns the manifest entries. Blobs are emitted as .cso by default so the
    existing DX12 decompile path can consume them unchanged."""
    with open(fxc_path, "rb") as f:
        data = f.read()
    blobs = scan(data)
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    for b in blobs:
        fn = f"{b.index:02d}_{safe_name(b.name)}{ext}"
        with open(os.path.join(out_dir, fn), "wb") as fo:
            fo.write(data[b.offset:b.offset + b.size])
        entries.append({"index": b.index, "name": b.name, "file": fn,
                        "offset": b.offset, "size": b.size})
    with open(os.path.join(out_dir, MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"source": os.path.basename(fxc_path), "blobs": entries}, f, indent=2)
    return entries


def repack(orig_fxc_path: str, blob_dir: str, out_fxc_path: str,
           ext: str = ".cso", log=None) -> int:
    """Rebuild the container from the ORIGINAL file, splicing in any edited
    blob found in blob_dir (matched by the NN_Name<ext> emitted by unpack).
    Handles size changes by patching the u16 record size. Returns count
    replaced. The original is used as the authority for layout/preludes, so
    only the blobs you actually changed are swapped."""
    with open(orig_fxc_path, "rb") as f:
        data = f.read()
    blobs = scan(data)

    # map index -> edited blob path, if present
    edited: dict[int, str] = {}
    for b in blobs:
        cand = os.path.join(blob_dir, f"{b.index:02d}_{safe_name(b.name)}{ext}")
        if os.path.exists(cand):
            edited[b.index] = cand

    out = bytearray()
    prev_end = 0
    replaced = 0
    for b in blobs:
        out += data[prev_end:b.offset]           # prelude (record header)
        rec_off_rel = (b.record_size_offset - prev_end) if b.record_size_offset is not None else None
        if b.index in edited:
            with open(edited[b.index], "rb") as f:
                new = f.read()
            if new[:4] != DXBC_MAGIC:
                raise ValueError(f"{edited[b.index]} is not a DXBC/.cso blob")
            same = (new == data[b.offset:b.offset + b.size])
            out += new
            if rec_off_rel is not None:
                pos = len(out) - len(new) - (b.offset - prev_end) + rec_off_rel
                if len(new) > 0xFFFF:
                    raise ValueError(f"blob {b.index} '{b.name}' is 0x{len(new):X} bytes; "
                                     f"exceeds rgxd u16 record-size limit (0xFFFF)")
                out[pos:pos + 2] = (len(new) & 0xFFFF).to_bytes(2, "little")
            if not same:
                replaced += 1
                if log:
                    log(f"  replaced blob {b.index} '{b.name}' "
                        f"(0x{b.size:X} -> 0x{len(new):X})")
        else:
            out += data[b.offset:b.offset + b.size]
        prev_end = b.offset + b.size
    out += data[prev_end:]

    # verify the result re-scans to the expected blob count
    if len(scan(bytes(out))) != len(blobs):
        raise ValueError("rebuild verification failed: blob count changed")

    with open(out_fxc_path, "wb") as f:
        f.write(bytes(out))
    return replaced
