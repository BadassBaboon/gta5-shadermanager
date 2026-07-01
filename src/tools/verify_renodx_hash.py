"""
Verification: does RenoDX's runtime shader hash (CRC32 over the DXBC/DXIL blob
the game hands to CreatePipelineState) match a CRC32 over the AWC shader_binary?

RenoDX uses standard zlib/IEEE CRC-32 (poly 0xEDB88320, init 0xFFFFFFFF,
final XOR ~crc) -- see RenoDX/src/utils/hash.hpp ComputeCRC32(). That is
byte-for-byte identical to Python's zlib.crc32.

This script parses an .awc, prints crc32 + effect grouping for a sample of
shaders, and dumps a few .cso blobs so you can compare bytes/crc against a
devkit "Dump Binary" from the running game.
"""

import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # src/ (holds the awclib package)
from awclib.parser import parse_awc_file


def renodx_hash(data: bytes) -> int:
    """Match RenoDX utils::hash::ComputeCRC32 (zlib/IEEE CRC-32)."""
    return zlib.crc32(data) & 0xFFFFFFFF


def build_hash_to_effects(awc):
    """Map renodx-crc32 -> (shader_name, stage, [effect names])."""
    stage_lists = {
        "vs": awc.vertex_shaders,
        "ps": awc.pixel_shaders,
        "gs": awc.geometry_shaders,
        "ds": awc.domain_shaders,
        "hs": awc.hull_shaders,
        "cs": awc.compute_shaders,
    }
    # crc -> info
    table = {}
    for stage, shaders in stage_lists.items():
        for sh in shaders:
            if not sh.shader_binary:
                continue
            crc = renodx_hash(sh.shader_binary)
            info = table.setdefault(
                crc,
                {"name": sh.name, "stage": stage, "effects": set(),
                 "size": len(sh.shader_binary),
                 "magic": sh.shader_binary[:4]},
            )
            # collision sanity: same crc, different bytecode name
            if info["name"] != sh.name:
                info.setdefault("aliases", set()).add(sh.name)

    # attach effect names by walking each effect's per-stage index lists
    idx_attr = {
        "vs": ("vs_indices", awc.vertex_shaders),
        "ps": ("ps_indices", awc.pixel_shaders),
        "gs": ("gs_indices", awc.geometry_shaders),
        "ds": ("ds_indices", awc.domain_shaders),
        "hs": ("hs_indices", awc.hull_shaders),
        "cs": ("cs_indices", awc.compute_shaders),
    }
    for eff in awc.effects:
        for stage, (attr, shaders) in idx_attr.items():
            for gi in getattr(eff, attr):
                if 0 <= gi < len(shaders):
                    sh = shaders[gi]
                    if not sh.shader_binary:
                        continue
                    crc = renodx_hash(sh.shader_binary)
                    if crc in table:
                        table[crc]["effects"].add(eff.name)
    return table


def main():
    awc_path = sys.argv[1] if len(sys.argv) > 1 else \
        str(Path(__file__).parent / "awc_files" / "sga_win32_60_final.awc")
    print(f"Parsing {awc_path} ...")
    awc = parse_awc_file(awc_path)
    print(f"magic={awc.magic}  total_shaders={awc.total_shader_count}  "
          f"effects={len(awc.effects)}")
    print(f"  VS={len(awc.vertex_shaders)} PS={len(awc.pixel_shaders)} "
          f"GS={len(awc.geometry_shaders)} DS={len(awc.domain_shaders)} "
          f"HS={len(awc.hull_shaders)} CS={len(awc.compute_shaders)}")

    table = build_hash_to_effects(awc)
    print(f"\nUnique CRC32 hashes: {len(table)}")

    # header sanity: all should be DXBC or DXIL
    magics = {}
    for info in table.values():
        magics[info["magic"]] = magics.get(info["magic"], 0) + 1
    print("Bytecode container magics:", {k.decode('latin-1', 'replace'): v
                                          for k, v in magics.items()})

    # show a sample, prefer vehicle_paint effects the user mentioned
    print("\n--- Sample: shaders in effects matching 'vehicle_paint' ---")
    shown = 0
    for crc, info in table.items():
        effs = sorted(info["effects"])
        if any("vehicle_paint" in e for e in effs):
            print(f"  0x{crc:08x}  {info['stage']:>2}  {info['name']:<28} "
                  f"-> {', '.join(effs)}")
            shown += 1
            if shown >= 20:
                break
    if shown == 0:
        print("  (none found - showing first 15 PS instead)")
        for crc, info in list(table.items())[:15]:
            if info["stage"] == "ps":
                print(f"  0x{crc:08x}  {info['name']:<28} "
                      f"-> {', '.join(sorted(info['effects'])) or '(no effect)'}")

    # dump a couple of .cso for byte-compare vs devkit "Dump Binary"
    out_dir = Path(__file__).parent / "verify_cso_dump"
    out_dir.mkdir(exist_ok=True)
    dumped = 0
    for crc, info in table.items():
        if info["stage"] != "ps":
            continue
        # re-find the bytecode for this crc
        for sh in awc.pixel_shaders:
            if sh.shader_binary and renodx_hash(sh.shader_binary) == crc:
                p = out_dir / f"0x{crc:08x}_{info['name'].replace(chr(92),'_')}.cso"
                p.write_bytes(sh.shader_binary)
                print(f"\nDumped {p.name} ({len(sh.shader_binary)} bytes, "
                      f"crc 0x{crc:08x})")
                dumped += 1
                break
        if dumped >= 3:
            break

    print("\nNext: in the running game, open RenoDX devkit Shaders tab, find "
          "one of the dumped hashes, use More > Dump Binary, and compare the "
          "bytes (or its crc) against the .cso here. If equal, the join key is "
          "confirmed.")


if __name__ == "__main__":
    main()
