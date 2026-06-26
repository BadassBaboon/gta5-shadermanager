"""
Rebuild a GTA5 Enhanced .awc shader library with shaders modified inside
RenoDX's live folder.

Workflow this supports:
  1. In RenoDX you edit a shader as `<renodx-dev>/live/0x<HASH>.<profile>.hlsl`
     (or drop a precompiled `0x<HASH>....cso`). HASH is the ORIGINAL shader's
     RenoDX hash = CRC32 of the original DXBC/DXIL blob.
  2. This script compiles each .hlsl with dxc (DX12/SM6), finds which AWC slot
     that original hash belongs to by matching CRC32 against every shader in the
     .awc, swaps the bytecode (awclib.import_shader), and writes a NEW .awc
     (the original is backed up, never overwritten unless --in-place).

Why CRC32 matching (not the shader name): AWC shader names are NOT unique --
the same name is compiled per-effect into distinct blobs. The hash is unique
per blob, so it pins the exact slot(s). If a blob is shared by several slots
(identical bytecode), all of them are updated.

Usage:
  python rebuild_awc_from_live.py --live "<path to renodx-dev/live>"
         [--awc awc_files/sga_win32_60_final.awc ...]
         [--out-dir awc_files]
         [--in-place]            overwrite the source .awc (after backup)
         [--update-metadata]     rebuild cbuffer/register reflection from the new blob
         [--regen-sidecar]       re-run export_shader_effects.py afterwards
         [--dry-run]             report what would change, write nothing
"""

import argparse
import configparser
import shutil
import subprocess
import sys
import tempfile
import zlib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from awclib.parser import parse_awc_file
from awclib.awc_writer import import_shader, rebuild_awc

_HERE = Path(__file__).parent
_DEFAULT_AWCS = [
    _HERE / "awc_files" / "sga_win32_60_final.awc",
    _HERE / "awc_files" / "sga_win32_60_final_init.awc",
]

# AWC stage key (awc_writer.import_shader) keyed by the 2-letter profile prefix.
_PROFILE_TO_STAGE = {
    "vs": "vertex", "ps": "pixel", "gs": "geometry",
    "ds": "domain", "hs": "hull", "cs": "compute",
}
_STAGE_ATTR = {
    "vertex": "vertex_shaders", "pixel": "pixel_shaders",
    "geometry": "geometry_shaders", "domain": "domain_shaders",
    "hull": "hull_shaders", "compute": "compute_shaders",
}

# --- CoreFX injection exclusions (what must stay STOCK in the AWC) ---
# VS injection works fine once the original root signature is transplanted (see
# transplant_rootsig). The ONLY shaders kept stock are the grass + near-tree (non-LOD)
# wind VS: their enhanced wind is always-on in the no-addon route (no UI toggle) and would
# desync from RT vegetation shadows. EVERYTHING ELSE is injected -- incl. the tree-LOD VS
# (0xEC22CADB/0xFE211B4F; LODs get no RT shadows -> no desync), the corona VS (0x9EF6111A)
# + ptxgpu particle VS (0x7A61A92D), and all PS/CS (corona/ptxgpu/puddle PS included).
#   0x730AB704 VS_PropFoliageDeferred  near-tree wind
#   0xDC532AF9 VS_Transform_REC        grass wind
#   0xDE966016 VS_Transform            grass wind
# Override via --exclude-hash / --exclude-stages / --exclude-name / --no-exclude.
_DEFAULT_EXCLUDE_HASHES = (0x730AB704, 0xDC532AF9, 0xDE966016)
_DEFAULT_EXCLUDE_STAGES = ()
_DEFAULT_EXCLUDE_NAME_SUBSTR = ()


def renodx_hash(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def load_dxc_path() -> Path:
    cfg = configparser.ConfigParser()
    cfg.read(_HERE / "settings.ini")
    rel = cfg.get("Paths", "dx12_compiler_path", fallback="dxcompilers/dxc.exe")
    return (_HERE / rel).resolve()


def parse_live_filename(path: Path):
    """Return (original_hash:int, profile:str|None) or (None, None) if not a
    RenoDX shader file. Mirrors RenoDX's watcher naming:
       0x<8 hex>.<profile>.hlsl    e.g. 0x06E3253E.ps_6_0.hlsl
       0x<8 hex>...cso             (binary; profile not required)
    """
    ext = path.suffix.lower()
    if ext not in (".hlsl", ".cso", ".spv"):
        return None, None
    stem = path.stem  # strips final ext only -> "0x06E3253E.ps_6_0" for .hlsl
    name = path.name
    if not name[:2].lower() == "0x":
        return None, None
    hash_str = name[2:10]
    try:
        original_hash = int(hash_str, 16)
    except ValueError:
        return None, None

    profile = None
    if ext == ".hlsl":
        # stem like "0x06E3253E.ps_6_0" -> last dotted token is the profile
        token = stem.split(".")[-1]
        parts = token.split("_")
        if len(parts) == 3 and parts[0].lower() in _PROFILE_TO_STAGE:
            profile = token
    return original_hash, profile


def compile_hlsl(dxc: Path, src: Path, profile: str, out: Path, log) -> bool:
    # Matches the shadermanager's DX12 path, plus -Qstrip_reflect so the container
    # has no STAT chunk -- the stock GTA shaders are reflection-stripped, and the AWC
    # carries its own reflection metadata, so the STAT chunk is just dead weight.
    cmd = [str(dxc), "-T", profile, "-Qstrip_reflect", "-Fo", str(out), str(src)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        log(f"    dxc FAILED: {res.stderr.strip()}")
        return False
    return True


# NOTE: root-signature preservation now lives in awclib.awc_writer.import_shader
# (transplant_rootsig), so EVERY injection path -- this CLI and the GUI's single-shader
# import -- validates/preserves the root signature and refuses crash-prone swaps.


def gather_live_shaders(live_dir: Path, dxc: Path, tmp_dir: Path, log,
                        exclude_stages=(), exclude_hashes=()):
    """Return dict original_hash -> (cso_path, source_path).

    exclude_stages: shader stages (e.g. "vertex") to skip entirely.
    exclude_hashes: exact RenoDX hashes to skip entirely.
    Skipped files are never compiled, so they never reach the AWC.
    """
    exclude_hashes = set(exclude_hashes or ())
    out = {}
    for path in sorted(live_dir.rglob("*")):
        if not path.is_file():
            continue
        original_hash, profile = parse_live_filename(path)
        if original_hash is None:
            continue
        if original_hash in exclude_hashes:
            log(f"  SKIP {path.name}: 0x{original_hash:08x} excluded (kept stock)")
            continue
        if path.suffix.lower() == ".hlsl":
            if profile is None:
                log(f"  SKIP {path.name}: cannot read SM profile from name")
                continue
            stage = _PROFILE_TO_STAGE.get(profile.split("_")[0].lower())
            if stage in exclude_stages:
                log(f"  SKIP {path.name}: {stage} shaders excluded (AWC rejects them)")
                continue
            cso = tmp_dir / f"0x{original_hash:08x}.cso"
            log(f"  compile {path.name}  (-T {profile})")
            if not compile_hlsl(dxc, path, profile, cso, log):
                continue
            out[original_hash] = (cso, path)
        else:
            log(f"  use binary {path.name}")
            out[original_hash] = (path, path)
    return out


def build_crc_index(awc):
    """crc32(blob) -> list of (stage_key, index)."""
    index = {}
    for stage_key, attr in _STAGE_ATTR.items():
        for i, sh in enumerate(getattr(awc, attr)):
            if not sh.shader_binary:
                continue
            index.setdefault(renodx_hash(sh.shader_binary), []).append((stage_key, i))
    return index


def run_rebuild(live, awc=None, out_dir=None, in_place=False,
                update_metadata=False, dxc_path=None, log=print,
                dry_run=False, exclude_stages=_DEFAULT_EXCLUDE_STAGES,
                exclude_name_substr=_DEFAULT_EXCLUDE_NAME_SUBSTR,
                exclude_hashes=_DEFAULT_EXCLUDE_HASHES):
    """Core rebuild routine, callable from the CLI or the GUI.

    Args:
        live: path to the renodx-dev/live folder.
        awc: list of .awc paths (defaults to the two bundled archives).
        out_dir: where to write modified .awc + backups (defaults to awc_files).
        in_place: overwrite the source .awc (after backup) instead of *_modified.awc.
        update_metadata: rebuild cbuffer/register reflection from the new blob.
        dxc_path: dxc.exe path (defaults to settings.ini).
        log: callable(str) for progress lines.
        dry_run: report only, write nothing.

    Returns a result dict: {written:[paths], changes:int, matched:int,
                            unmatched:[(hash,name)], live_count:int}.
    """
    result = {"written": [], "changes": 0, "matched": 0,
              "unmatched": [], "live_count": 0, "excluded": 0, "error": None}
    # exclude_name_substr is matched case-insensitively
    exclude_name_substr = tuple(s.lower() for s in (exclude_name_substr or ()))
    exclude_stages = tuple(exclude_stages or ())
    exclude_hashes = set(exclude_hashes or ())

    live_dir = Path(live)
    if not live_dir.is_dir():
        result["error"] = f"Live folder not found: {live_dir}"
        log("ERROR: " + result["error"])
        return result

    dxc = Path(dxc_path) if dxc_path else load_dxc_path()
    if not dxc.exists():
        result["error"] = f"dxc.exe not found at {dxc}"
        log("ERROR: " + result["error"])
        return result

    awc_paths = awc if awc else [str(p) for p in _DEFAULT_AWCS]
    out_dir = Path(out_dir) if out_dir else (_HERE / "awc_files")
    backups_dir = out_dir / "backups"

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        log(f"Scanning live folder: {live_dir}")
        live_shaders = gather_live_shaders(live_dir, dxc, tmp_dir, log,
                                           exclude_stages=exclude_stages,
                                           exclude_hashes=exclude_hashes)
        result["live_count"] = len(live_shaders)
        if not live_shaders:
            log("No RenoDX shader files found in the live folder. Nothing to do.")
            return result
        log(f"Found {len(live_shaders)} modified shader(s).")

        unmatched = set(live_shaders.keys())

        for awc_path in awc_paths:
            awc_path = Path(awc_path)
            if not awc_path.exists():
                log(f"SKIP (missing): {awc_path}")
                continue
            log(f"\nAWC: {awc_path.name}")
            awc_obj = parse_awc_file(str(awc_path))
            crc_index = build_crc_index(awc_obj)

            changes = 0
            for original_hash, (cso_path, src_path) in live_shaders.items():
                slots = crc_index.get(original_hash, [])
                if not slots:
                    continue
                unmatched.discard(original_hash)
                for stage_key, idx in slots:
                    sh = getattr(awc_obj, _STAGE_ATTR[stage_key])[idx]
                    sh_name = sh.name or ""
                    low = sh_name.lower()
                    if stage_key in exclude_stages or any(s in low for s in exclude_name_substr):
                        log(f"  [SKIP] 0x{original_hash:08x} -> {stage_key}[{idx}]  {sh_name}  (excluded)")
                        result["excluded"] += 1
                        continue
                    if dry_run:
                        ok, msg = (True, "(dry-run)")
                    else:
                        # import_shader splices the stock root signature onto the new
                        # bytecode and REFUSES (returns False) if the modified shader is
                        # root-sig-incompatible -- preventing a crash-prone injection.
                        ok, msg = import_shader(
                            awc_obj, stage_key, idx, str(cso_path),
                            update_metadata=update_metadata, dxc_path=str(dxc))
                    if not ok and "root signature" in msg.lower():
                        # left stock by design (not an error)
                        log(f"  [STOCK] 0x{original_hash:08x} -> {stage_key}[{idx}]  {sh_name}  ({msg})")
                        result["rootsig_skipped"] = result.get("rootsig_skipped", 0) + 1
                        continue
                    tag = "OK " if ok else "ERR"
                    log(f"  [{tag}] 0x{original_hash:08x} -> {stage_key}[{idx}]  {sh_name}  {msg}")
                    if ok:
                        changes += 1

            if changes == 0:
                log("  no matching shaders in this AWC.")
                continue

            result["changes"] += changes

            if dry_run:
                log(f"  would update {changes} slot(s).")
                continue

            backups_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = backups_dir / f"{awc_path.stem}.{stamp}.bak.awc"
            shutil.copy2(awc_path, backup)
            log(f"  backup -> {backup}")

            out_path = awc_path if in_place else (out_dir / f"{awc_path.stem}_modified.awc")
            rebuild_awc(awc_obj, str(awc_path), str(out_path))
            log(f"  wrote {changes} change(s) -> {out_path}")
            result["written"].append(str(out_path))

        result["matched"] = len(live_shaders) - len(unmatched)
        result["unmatched"] = [(h, live_shaders[h][1].name) for h in sorted(unmatched)]
        if unmatched:
            log("\nWARNING: these live hashes matched no shader in any AWC "
                "(wrong AWC set, or already-modified bytecode):")
            for h, name in result["unmatched"]:
                log(f"  0x{h:08x}  ({name})")

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", required=True, help="Path to renodx-dev/live folder")
    ap.add_argument("--awc", nargs="*", default=[str(p) for p in _DEFAULT_AWCS])
    ap.add_argument("--out-dir", default=str(_HERE / "awc_files"))
    ap.add_argument("--in-place", action="store_true")
    ap.add_argument("--update-metadata", action="store_true")
    ap.add_argument("--regen-sidecar", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--exclude-stages", nargs="*",
                    default=list(_DEFAULT_EXCLUDE_STAGES),
                    help="shader stages to never inject (default: none)")
    ap.add_argument("--exclude-name", nargs="*",
                    default=list(_DEFAULT_EXCLUDE_NAME_SUBSTR),
                    help="case-insensitive AWC-name substrings to skip (default: none)")
    ap.add_argument("--exclude-hash", nargs="*",
                    default=[f"{h:08x}" for h in _DEFAULT_EXCLUDE_HASHES],
                    help="exact RenoDX hashes (hex) to keep stock "
                         "(default: the 5 wind/particle/corona VS)")
    ap.add_argument("--no-exclude", action="store_true",
                    help="disable all exclusions (inject everything)")
    args = ap.parse_args()

    excl_stages = () if args.no_exclude else tuple(args.exclude_stages)
    excl_names = () if args.no_exclude else tuple(args.exclude_name)
    excl_hashes = () if args.no_exclude else tuple(
        int(h, 16) for h in args.exclude_hash)

    result = run_rebuild(
        live=args.live, awc=args.awc, out_dir=args.out_dir,
        in_place=args.in_place, update_metadata=args.update_metadata,
        dry_run=args.dry_run, log=print,
        exclude_stages=excl_stages, exclude_name_substr=excl_names,
        exclude_hashes=excl_hashes)

    if result["error"]:
        return 2

    if args.regen_sidecar and result["written"] and not args.dry_run:
        print("\nRegenerating sidecar...")
        subprocess.run([sys.executable, str(_HERE / "export_shader_effects.py")])

    return 0


if __name__ == "__main__":
    sys.exit(main())
