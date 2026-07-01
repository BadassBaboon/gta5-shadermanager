"""
Export a RenoDX-devkit sidecar that maps each shader's RenoDX hash
(CRC32 over the DXBC/DXIL blob) to its shader name and the effect(s) it
belongs to, parsed from one or more GTA5E .awc shader libraries.

The RenoDX devkit loads this file from:  <game_exe_dir>/renodx-dev/shader_effects.json

Format (compact, effect names interned):
{
  "version": 1,
  "source": ["sga_win32_60_final.awc", ...],
  "effects": ["vehicle_paint1", "vehicle_paint2", ...],   # interned names
  "shaders": {
     "df4eb9fd": {"n": "VS_VehicleTransform_..._Wrapped", "s": "vs", "e": [23, 41]}
  }
}
  key  = lowercase 8-hex RenoDX CRC32 (no 0x)
  n    = shader (entry-point) name from the AWC
  s    = stage: vs|ps|gs|ds|hs|cs
  e    = indices into "effects"

Usage:
  python export_shader_effects.py [out.json] [awc1.awc awc2.awc ...]
  python export_shader_effects.py --install   # write straight to the game's renodx-dev dir (uses settings.ini game path if present)
"""

import json
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # src/ (holds the awclib package)
from awclib.parser import parse_awc_file

_HERE = Path(__file__).parent
_DEFAULT_AWCS = [
    _HERE / "awc_files" / "sga_win32_60_final.awc",
    _HERE / "awc_files" / "sga_win32_60_final_init.awc",
]

_STAGES = [
    ("vs", "vertex_shaders", "vs_indices"),
    ("ps", "pixel_shaders", "ps_indices"),
    ("gs", "geometry_shaders", "gs_indices"),
    ("ds", "domain_shaders", "ds_indices"),
    ("hs", "hull_shaders", "hs_indices"),
    ("cs", "compute_shaders", "cs_indices"),
]


def renodx_hash(data: bytes) -> int:
    """Match RenoDX utils::hash::ComputeCRC32 (zlib/IEEE CRC-32)."""
    return zlib.crc32(data) & 0xFFFFFFFF


def build_table(awc_paths):
    # crc -> {"n": name, "s": stage, "e": set(effect_name)}
    table = {}
    sources = []
    for awc_path in awc_paths:
        awc_path = Path(awc_path)
        if not awc_path.exists():
            print(f"  skip (not found): {awc_path}")
            continue
        print(f"  parsing {awc_path.name} ...")
        awc = parse_awc_file(str(awc_path))
        sources.append(awc_path.name)

        stage_shaders = {s: getattr(awc, attr) for s, attr, _ in _STAGES}

        # 1) register every shader by crc
        for stage, shaders in stage_shaders.items():
            for sh in shaders:
                if not sh.shader_binary:
                    continue
                crc = renodx_hash(sh.shader_binary)
                entry = table.get(crc)
                if entry is None:
                    table[crc] = {"n": sh.name, "s": stage, "e": set()}

        # 2) attach effect membership
        for eff in awc.effects:
            for stage, _, idx_attr in _STAGES:
                shaders = stage_shaders[stage]
                for gi in getattr(eff, idx_attr):
                    if 0 <= gi < len(shaders):
                        sh = shaders[gi]
                        if not sh.shader_binary:
                            continue
                        crc = renodx_hash(sh.shader_binary)
                        ent = table.get(crc)
                        if ent is not None:
                            ent["e"].add(eff.name)
    return table, sources


def to_sidecar(table, sources):
    # intern effect names
    effect_index = {}
    effects = []

    def idx(name):
        i = effect_index.get(name)
        if i is None:
            i = len(effects)
            effect_index[name] = i
            effects.append(name)
        return i

    shaders = {}
    for crc, ent in table.items():
        e_indices = sorted(idx(n) for n in ent["e"])
        shaders[f"{crc:08x}"] = {"n": ent["n"], "s": ent["s"], "e": e_indices}

    return {
        "version": 1,
        "source": sources,
        "effects": effects,
        "shaders": shaders,
    }


def export_sidecar(awc_paths, out_path, pretty=False, log=print):
    """Build the sidecar and write it. Callable from CLI or GUI.

    Returns {"shaders": int, "effects": int, "path": str}.
    """
    log(f"Building sidecar from {len(awc_paths)} AWC file(s)...")
    table, sources = build_table([Path(p) for p in awc_paths])
    sidecar = to_sidecar(table, sources)
    text = (json.dumps(sidecar, indent=2) if pretty
            else json.dumps(sidecar, separators=(",", ":")))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    stats = {"shaders": len(sidecar["shaders"]),
             "effects": len(sidecar["effects"]),
             "path": str(out_path)}
    log(f"Wrote {out_path}  ({stats['shaders']} shaders, {stats['effects']} effects)")
    return stats


def main():
    args = sys.argv[1:]
    pretty = "--pretty" in args
    args = [a for a in args if a != "--pretty"]

    out_path = _HERE / "shader_effects.json"
    awc_paths = list(_DEFAULT_AWCS)

    if args and not args[0].startswith("--"):
        out_path = Path(args[0])
        if len(args) > 1:
            awc_paths = args[1:]

    print(f"Building sidecar from {len(awc_paths)} AWC file(s)...")
    table, sources = build_table(awc_paths)
    sidecar = to_sidecar(table, sources)

    if pretty:
        out_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    else:
        out_path.write_text(json.dumps(sidecar, separators=(",", ":")), encoding="utf-8")
    n_eff = len(sidecar["effects"])
    n_sh = len(sidecar["shaders"])
    size_kb = out_path.stat().st_size / 1024
    print(f"\nWrote {out_path}")
    print(f"  shaders={n_sh}  effects={n_eff}  size={size_kb:.1f} KB")
    # quick stats: how many shaders map to >1 effect
    multi = sum(1 for s in sidecar["shaders"].values() if len(s["e"]) > 1)
    none = sum(1 for s in sidecar["shaders"].values() if len(s["e"]) == 0)
    print(f"  shaders in >1 effect: {multi}   shaders with no effect: {none}")
    print(f"\nCopy this file to <game_exe_dir>/renodx-dev/shader_effects.json")


if __name__ == "__main__":
    main()
