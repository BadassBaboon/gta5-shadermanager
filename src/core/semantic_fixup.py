"""Restore original I/O signatures in decompiled DX12 HLSL.

The dxil-spirv -> spirv-cross pipeline mangles a shader's input/output
signatures in two ways that break pairing a recompiled stage with an original
counterpart stage (D3D12 wires interpolants by signature row and validates by
semantic name+index):

1. Semantic names are discarded: every non-system-value element is renumbered
   sequentially as TEXCOORD1, TEXCOORD2, ... (e.g. RDR1's terrain PS actually
   consumes POSITION1 + TEXCOORD0..7).
2. Row order is changed: SV_Position is emitted as the LAST struct member, so
   every element lands one register row lower than in the original blob.

Either mismatch corrupts rendering as soon as a recompiled shader is paired
with an original stage (the normal case when replacing a single shader). The
same applies to VS inputs vs the game's vertex input layouts.

This module reads the ORIGINAL container's signature tables (dxc -dumpbin) and
rewrites the generated SPIRV_Cross_Input/Output structs to be
signature-identical: original semantic names AND original declaration (row)
order. It is idempotent: re-running it on an already-fixed file is a no-op.
"""

import os
import re
import subprocess

# Rows of a dumpbin signature table, e.g.
# ; TEXCOORD                 3   xy          6     NONE   float   xy
_SIG_ROW = re.compile(
    r"^;\s+([A-Za-z_][A-Za-z0-9_]*)\s+(\d+)\s+[xyzw ]{1,6}\s+(\d+)\s+(\w+)"
)

# Struct member with a semantic, e.g. "    float4 POSITION_1 : TEXCOORD1;"
_MEMBER = re.compile(r"^(\s*\S+\s+\S+\s*:\s*)([A-Za-z_][A-Za-z0-9_]*?)(\d*)(\s*;)")


def parse_signatures(dxc_path, cso_path):
    """Return ({'input': [(name, index, is_attribute)], 'output': [...]}, stage).

    Elements appear in table order (= register row order). is_attribute is
    True for plain attributes (SysValue column NONE) that fixup may rename;
    system values (POS, TARGET, ...) are kept for ordering only. Note RAGE
    names its VS position input literally "SV_Position" with SysValue NONE --
    the SysValue column, not the name, decides.
    """
    try:
        res = subprocess.run(
            [dxc_path, "-dumpbin", cso_path],
            capture_output=True, text=True, errors="ignore",
        )
    except OSError:
        return None, None
    if res.returncode != 0:
        return None, None

    sigs = {"input": [], "output": []}
    section = None
    stage = None
    for line in res.stdout.splitlines():
        low = line.strip().lower()
        if low.startswith("; input signature"):
            section = "input"
            continue
        if low.startswith("; output signature"):
            section = "output"
            continue
        if "pixel shader" in low:
            stage = stage or "ps"
        elif "vertex shader" in low:
            stage = stage or "vs"
        # The InterpMode tables re-use the section headers but their rows lack
        # the mask/register/sysvalue columns, so _SIG_ROW ignores them; stop
        # collecting entirely once the runtime-info block starts.
        if low.startswith("; pipeline runtime"):
            section = None
        if section:
            m = _SIG_ROW.match(line)
            if m:
                name, idx, sysvalue = m.group(1), int(m.group(2)), m.group(4)
                mask = re.match(r"^;\s+\S+\s+\d+\s+([xyzw ]{1,6})\s+\d+", line)
                width = len(mask.group(1).strip().replace(" ", "")) if mask else 4
                entry = (name, idx, sysvalue.upper() == "NONE", width)
                if entry not in sigs[section]:
                    sigs[section].append(entry)
    return sigs, stage


def _member_key(sem_name, sem_idx):
    return (sem_name.upper(), int(sem_idx) if sem_idx else 0)


def _fixup_struct(lines, struct_name, elements, log):
    """Rename and reorder the members of `struct <struct_name>` in place to
    match the original signature elements (in table order)."""
    start = None
    end = None
    for i, line in enumerate(lines):
        if start is None and re.match(rf"\s*struct\s+{re.escape(struct_name)}\b", line):
            start = i
        elif start is not None and line.strip().startswith("};"):
            end = i
            break
    if start is None or end is None:
        return 0

    member_lines = []  # line numbers of semantic-carrying members
    for i in range(start, end):
        if _MEMBER.match(lines[i]):
            member_lines.append(i)

    # Split members into renameable attributes (non-SV semantics, in
    # declaration order = decompiler location order) and system values.
    attr_lines = []
    for i in member_lines:
        m = _MEMBER.match(lines[i])
        if not m.group(2).upper().startswith("SV_") or (
            # Already-fixed VS inputs may carry an "SV_Position0" attribute
            # semantic (RAGE's name for its position input, SysValue NONE).
            any(n.upper() == m.group(2).upper() and a for n, _, a, _ in elements)
        ):
            attr_lines.append(i)

    attrs = [(n, i) for n, i, is_attr, _ in elements if is_attr]
    if len(attr_lines) != len(attrs):
        if log:
            log(f"  -> Semantic fixup skipped for {struct_name}: "
                f"{len(attr_lines)} members vs {len(attrs)} signature elements")
        return 0

    changed = 0
    # Pass 1: rename attribute semantics in declaration order.
    for line_no, (orig_name, orig_idx) in zip(attr_lines, attrs):
        m = _MEMBER.match(lines[line_no])
        new_sem = f"{orig_name}{orig_idx}"
        if f"{m.group(2)}{m.group(3)}" != new_sem:
            lines[line_no] = f"{m.group(1)}{new_sem}{m.group(4)}"
            changed += 1

    # Pass 2: rebuild the member block in original table order so every
    # element lands on its original register row (drivers wire interpolants
    # by row, not by name). Elements the decompiler dead-code-eliminated
    # (e.g. an unused SV_Position input that still occupies row 0 in the
    # original) are re-declared as unused placeholders to keep rows aligned.
    by_key = {}
    for i in member_lines:
        m = _MEMBER.match(lines[i])
        by_key[_member_key(m.group(2), m.group(3))] = lines[i]
    ordered = []
    for name, idx, is_attr, width in elements:
        k = (name.upper(), idx)
        if k in by_key:
            ordered.append(by_key.pop(k))
        else:
            # Integer system values must be declared uint or validation fails.
            uint_svs = ("SV_ISFRONTFACE", "SV_SAMPLEINDEX", "SV_PRIMITIVEID",
                        "SV_VERTEXID", "SV_INSTANCEID", "SV_RENDERTARGETARRAYINDEX",
                        "SV_VIEWPORTARRAYINDEX", "SV_COVERAGE")
            base = "uint" if name.upper() in uint_svs else "float"
            ftype = base if width == 1 else f"{base}{width}"
            # System values keep their bare semantic (SV_Position, not
            # SV_Position0) to stay valid HLSL in every context.
            sem = name if name.upper().startswith("SV_") and not is_attr else f"{name}{idx}"
            ordered.append(f"    {ftype} _unused_{name}_{idx} : {sem};")
    # Members not present in the table (unexpected) keep declaration order.
    ordered += [text for text in by_key.values()]

    if not member_lines:
        return changed
    lo, hi = min(member_lines), max(member_lines) + 1
    if [i for i in range(lo, hi) if i not in member_lines]:
        if log:
            log(f"  -> Row reorder skipped for {struct_name}: non-member lines in struct")
        return changed
    if lines[lo:hi] != ordered:
        lines[lo:hi] = ordered
        changed += 1
    return changed


def fixup_semantics(dxc_path, cso_path, hlsl_path, log=None):
    """Rewrite hlsl_path's I/O struct semantics and member order to match
    cso_path's original signatures. Returns number of lines changed."""
    if not (os.path.exists(cso_path) and os.path.exists(hlsl_path)):
        return 0
    sigs, stage = parse_signatures(dxc_path, cso_path)
    if not sigs:
        if log:
            log("  -> Semantic fixup skipped: could not read original signatures")
        return 0

    with open(hlsl_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    changed = _fixup_struct(lines, "SPIRV_Cross_Input", sigs["input"], log)
    if stage == "vs":
        changed += _fixup_struct(lines, "SPIRV_Cross_Output", sigs["output"], log)

    if changed:
        with open(hlsl_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        if log:
            log(f"  -> Semantic fixup: {changed} signature lines corrected")
    return changed


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Restore original I/O signatures in decompiled HLSL "
                    "(single file or batch over matching folders)")
    ap.add_argument("cso", help="original .cso/.dxbc blob, or a folder of them")
    ap.add_argument("hlsl", help="decompiled .hlsl file, or a folder of them")
    ap.add_argument("--dxc", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "dxcompilers", "dxc.exe"))
    args = ap.parse_args()

    def _log(msg):
        print(msg)

    if os.path.isdir(args.cso):
        total = 0
        for fn in sorted(os.listdir(args.hlsl)):
            if not fn.lower().endswith(".hlsl"):
                continue
            base = os.path.splitext(fn)[0]
            blob = None
            for ext in (".cso", ".dxbc"):
                cand = os.path.join(args.cso, base + ext)
                if os.path.exists(cand):
                    blob = cand
                    break
            if not blob:
                print(f"{fn}: no matching blob, skipped")
                continue
            print(f"{fn}:")
            total += fixup_semantics(args.dxc, blob, os.path.join(args.hlsl, fn), _log)
        print(f"done, {total} lines corrected")
    else:
        fixup_semantics(args.dxc, args.cso, args.hlsl, _log)
