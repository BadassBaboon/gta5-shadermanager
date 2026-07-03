"""Reconstruct RDR1 constant-buffer parameter names in decompiled HLSL.

RDR1's ORIGINAL DXIL blobs carry full reflection: every cbuffer member's real
name, type and byte offset (visible via `dxc -dumpbin`, Buffer Definitions).
The dxil-spirv -> spirv-cross decompile discards this, flattening each cbuffer
to one anonymous `float4 _..._m0[N]` array — which is why decompiled shaders
are a sea of `_Globals_m0[13u].w` mysteries.

This module reads the original blob's reflection and writes a parameter map
into the decompiled HLSL as a header comment block, e.g.:

    // $Globals -> _Globals_m0 (b0):
    //   [ 0].x    float    gMotionBlurScalar
    //   [ 3].y    float2   BrightSandHack
    //   [32].x    float    TimeOfDay

so `_Globals_m0[32u].x` can be read as `TimeOfDay` at a glance.

IMPORTANT: only ORIGINAL blobs have names. Recompiled blobs reflect our own
flattened declaration (member `_..._m0`), which this module detects and
refuses to use. Preference order for the reflection source: `<cso>.orig`
(the compile-time backup) first, then the .cso itself.
"""

import os
import re
import subprocess

MARKER = "RDR1 parameter map"

# ;       float3 Bumpiness_Terrain123;                  ; Offset:   64
_MEMBER_RE = re.compile(
    r"^;\s+([A-Za-z_][\w ]*?)\s+([A-Za-z_]\w*)(\[(\d+)\])?;\s+; Offset:\s+(\d+)\s*$")
# ; cbuffer $Globals
_CBUF_RE = re.compile(r"^; cbuffer (\S+)\s*$")
# ; $Globals   cbuffer  NA  NA  CB0  cb0  1
_BIND_RE = re.compile(r"^; (\S+)\s+cbuffer\s+.*\bcb(\d+)\b")

_TYPE_FLOATS = {
    "float": 1, "int": 1, "uint": 1, "bool": 1, "dword": 1,
    "float1": 1, "float2": 2, "float3": 3, "float4": 4,
    "int2": 2, "int3": 3, "int4": 4, "uint2": 2, "uint3": 3, "uint4": 4,
    "float2x2": 8, "float3x3": 12, "float3x4": 16, "float4x3": 16, "float4x4": 16,
}


def _type_floats(tname):
    t = tname.split()[-1]  # drop row_major etc.
    return _TYPE_FLOATS.get(t, 4)


def parse_reflection(dxc_path, cso_path):
    """Return (buffers, ok): buffers = {cbuffer_name: {"slot": N|None,
    "members": [(name, type, offset_bytes, total_floats)]}}. ok=False when the
    blob has no real names (i.e. it's one of our recompiles)."""
    try:
        res = subprocess.run([dxc_path, "-dumpbin", cso_path],
                             capture_output=True, text=True, errors="ignore")
    except OSError:
        return {}, False
    if res.returncode != 0:
        return {}, False

    buffers = {}
    current = None
    for line in res.stdout.splitlines():
        m = _CBUF_RE.match(line)
        if m:
            current = m.group(1)
            buffers.setdefault(current, {"slot": None, "members": []})
            continue
        b = _BIND_RE.match(line)
        if b and b.group(1) in buffers:
            buffers[b.group(1)]["slot"] = int(b.group(2))
            continue
        if current:
            mm = _MEMBER_RE.match(line)
            if mm:
                tname, name, _, arr, off = mm.group(1), mm.group(2), mm.group(3), mm.group(4), int(mm.group(5))
                if name == current.lstrip("$"):
                    continue  # the struct closer line
                count = int(arr) if arr else 1
                buffers[current]["members"].append(
                    (name, tname.strip() + (f"[{arr}]" if arr else ""),
                     off, _type_floats(tname) * count))

    # A blob is only a valid reference if some member ISN'T our own flattened
    # array naming (\w+_m0) — recompiles reflect exactly one such member.
    named = any(not re.fullmatch(r"\w*_m0", n)
                for buf in buffers.values() for (n, _, _, _) in buf["members"])
    return buffers, named


def _span(off_bytes, floats):
    """Human-readable slot/component span, e.g. '[ 3].y' or '[ 6]..[ 9]'."""
    slot = off_bytes // 16
    comp = (off_bytes % 16) // 4
    if floats <= 4 and comp + floats <= 4:
        letters = "xyzw"[comp:comp + floats]
        return f"[{slot:2}].{letters}"
    end_slot = (off_bytes + floats * 4 - 1) // 16
    return f"[{slot:2}]..[{end_slot:2}]"


def _decompiled_cbuffers(text):
    """Map register slot -> flattened array variable name in decompiled HLSL."""
    out = {}
    for m in re.finditer(
            r"cbuffer\s+(\w+)\s*:\s*register\(b(\d+)[^)]*\)\s*\{\s*float4\s+(\w+)\[",
            text):
        out[int(m.group(2))] = m.group(3)
    return out


def annotate_hlsl(dxc_path, ref_cso_path, hlsl_path, log=None):
    """Insert (or refresh) the parameter-map header in hlsl_path using the
    reflection of ref_cso_path (must be an ORIGINAL blob). Prefers
    ref_cso_path + '.orig' if that exists. Returns number of parameters
    annotated (0 = skipped)."""
    if os.path.exists(ref_cso_path + ".orig"):
        ref_cso_path = ref_cso_path + ".orig"
    if not (os.path.exists(ref_cso_path) and os.path.exists(hlsl_path)):
        return 0

    buffers, ok = parse_reflection(dxc_path, ref_cso_path)
    if not ok:
        if log:
            log("  -> Param map skipped: reference blob has no original names "
                "(recompiled?). Provide a pristine blob or .orig backup.")
        return 0

    with open(hlsl_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    slot_to_var = _decompiled_cbuffers(text)

    lines = [f"// ===== {MARKER} (reconstructed from original blob reflection) ====="]
    count = 0
    for bname, buf in buffers.items():
        if not buf["members"]:
            continue
        var = slot_to_var.get(buf["slot"], "?")
        lines.append(f"// {bname} -> {var} (b{buf['slot']}):")
        for (name, tname, off, floats) in buf["members"]:
            lines.append(f"//   {_span(off, floats):<12} {tname:<12} {name}")
            count += 1
    lines.append("// " + "=" * 68)
    block = "\n".join(lines) + "\n"

    # Idempotent: replace an existing map, else insert after the header comment.
    if MARKER in text:
        text = re.sub(
            r"// ===== " + re.escape(MARKER) + r".*?// =+\n",
            block, text, count=1, flags=re.DOTALL)
    else:
        m = re.match(r"((?:^//[^\n]*\n)+)", text)
        insert_at = m.end() if m else 0
        text = text[:insert_at] + block + text[insert_at:]

    with open(hlsl_path, "w", encoding="utf-8") as f:
        f.write(text)
    if log:
        log(f"  -> Param map: {count} parameters annotated")
    return count


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Annotate decompiled HLSL with original cbuffer parameter "
                    "names (single file or batch over matching folders)")
    ap.add_argument("cso", help="original .cso blob, or a folder of them")
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
            blob = os.path.join(args.cso, base + ".cso")
            if not os.path.exists(blob):
                print(f"{fn}: no matching blob, skipped")
                continue
            print(f"{fn}:")
            total += annotate_hlsl(args.dxc, blob, os.path.join(args.hlsl, fn), _log)
        print(f"done, {total} parameters annotated")
    else:
        annotate_hlsl(args.dxc, args.cso, args.hlsl, _log)
