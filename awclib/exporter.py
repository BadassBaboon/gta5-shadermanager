"""
CBuffer annotation exporter for decompiled HLSL shaders.
Matches AWC shader metadata to decompiled shaders and adds cbuffer macros and injections.
"""

import os
import re
from typing import Dict, Optional, Tuple
from .models import AWCFile, Shader, Register, ResourceType


def build_shader_lookup(awc: AWCFile) -> Dict[str, Shader]:
    """Build a lookup dict from shader name to shader object."""
    lookup = {}
    
    all_shaders = [
        ('VS', awc.vertex_shaders),
        ('PS', awc.pixel_shaders),
        ('GS', awc.geometry_shaders),
        ('DS', awc.domain_shaders),
        ('HS', awc.hull_shaders),
        ('CS', awc.compute_shaders),
    ]
    
    for prefix, shaders in all_shaders:
        for shader in shaders:
            # Store with original name
            lookup[shader.name] = shader
            # Also store lowercase for case-insensitive matching
            lookup[shader.name.lower()] = shader
    
    return lookup


def extract_shader_name(hlsl_content: str) -> Optional[str]:
    """Extract the original shader name from HLSL comment."""
    match = re.search(r'//\s*Original Entry Function:\s*(\S+)', hlsl_content)
    if match:
        return match.group(1)
    return None

def annotate_hlsl_file(hlsl_path: str, shader_lookup: Dict[str, Shader]) -> Tuple[bool, str]:
    """
    Injects cbuffer metadata directly into the decomp.exe generated HLSL.
    Missing variables are appended into the cbuffer block.
    Existing aliases are mapped via #define macros at the top of the file.
    """
    with open(hlsl_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    
    # Check if already annotated
    if "CBuffer Metadata Injected via AWC Manager" in content:
        return False, "Already annotated"
    
    shader_name = extract_shader_name(content)
    if not shader_name:
        return False, "Could not extract shader name"
    
    shader = shader_lookup.get(shader_name) or shader_lookup.get(shader_name.lower())
    if not shader:
        return False, f"No matching shader found for: {shader_name}"
    
    if not shader.registers:
        return False, "Shader has no register metadata"

    # Strategy: 
    # 1. Parse existing cbuffer blocks from the HLSL to see what decomp.exe created
    # 2. Match exact packoffsets (e.g. c013.x) to AWC variables
    # 3. Create #defines for the ones that match
    # 4. Inject the ones that decomp.exe dropped back into the cbuffer block as pure code
    
    lines = content.split('\n')
    new_lines = []
    
    macros = []
    macros.append("// ============================================================")
    macros.append(f"// CBuffer Metadata Injected via AWC Manager (Shader: {shader.name})")
    macros.append("// ============================================================")
    
    in_cbuffer = False
    current_cbuffer_name = ""
    current_cbuffer_slot = ""
    current_cbuffer_space = ""
    
    cbuffer_end_idx = -1
    
    # Map out the AWC metadata for quick lookup
    awc_cbs = {} # (slot, space) -> dict of byte_offset -> (AWC CBuffer Var)
    awc_srv_uav_reg = {} # (prefix, slot, space) -> str Name
    
    for reg in shader.registers:
        slot = str(reg.register_slot)
        space = str(reg.register_space)
        
        # Determine prefix type for SRV/UAV/Samplers
        prefix = ResourceType.get_register_prefix(reg.resource_type)
        
        awc_srv_uav_reg[(prefix, slot, space)] = reg.reg_name
        
        if reg.cbuffers:
            key = (slot, space)
            awc_cbs[key] = {}
            for cb in reg.cbuffers:
                awc_cbs[key][cb.pack_offset] = cb

    import math
    
    for i, line in enumerate(lines):
        # Check for start of cbuffer
        # e.g. cbuffer cb5 : register(b5) {
        # e.g. cbuffer cb12_space1 : register(b12, space1) {
        cb_match = re.match(r'^\s*cbuffer\s+(\w+)\s*:\s*register\(b(\d+)(?:,\s*space(\d+))?\)\s*\{', line)
        if cb_match:
            in_cbuffer = True
            current_cbuffer_name = cb_match.group(1)
            current_cbuffer_slot = cb_match.group(2)
            current_cbuffer_space = cb_match.group(3) if cb_match.group(3) else "0"
            new_lines.append(line)
            continue
            
        if in_cbuffer:
            if re.match(r'^\s*};\s*$', line):
                # End of cbuffer block - Inject missing AWC vars here before closing!
                key = (current_cbuffer_slot, current_cbuffer_space)
                if key in awc_cbs:
                    missing_vars = awc_cbs[key]
                    if missing_vars:
                        # Sort by pack_offset visually
                        sorted_offsets = sorted(missing_vars.keys())
                        for b_off in sorted_offsets:
                            cb = missing_vars[b_off]
                            # Only inject if we haven't popped it (meaning decomp.exe didn't emit it)
                            array_str = f"[{cb.array_size}]" if cb.array_size > 1 else ""
                            c_index = b_off // 16
                            component = ['x', 'y', 'z', 'w'][(b_off % 16) // 4] if b_off % 16 > 0 else "x"
                            new_lines.append(f"  {cb.type_name} {cb.cbuffer_name}{array_str} : packoffset(c{c_index:03d}.{component}); // [AWC Injected]")
                new_lines.append(line)
                in_cbuffer = False
                continue

            # Parse the variables decomp.exe generated
            # e.g. float globalScalars2_x : packoffset(c013.x);
            # e.g. float4 g_rage_matrices_000[4] : packoffset(c000.x);
            var_match = re.match(r'^\s*([\w\d_]+)\s+([\w\d_]+)(?:\[(\d+)\])?(?:\s*\[\d+\])*\s*:\s*packoffset\(c(\d+)\.([xyzw])\);(\s*//.*)?$', line)
            if var_match:
                v_type = var_match.group(1)
                v_name = var_match.group(2)
                v_reg = int(var_match.group(4))
                v_comp = {'x': 0, 'y': 4, 'z': 8, 'w': 12}[var_match.group(5)]
                
                byte_offset = (v_reg * 16) + v_comp
                
                key = (current_cbuffer_slot, current_cbuffer_space)
                if key in awc_cbs and byte_offset in awc_cbs[key]:
                    # We matched a decomp.exe variable to an AWC metadata offset!
                    awc_var = awc_cbs[key].pop(byte_offset) # Remove from dict so we know it was emitted
                    
                    array_str = f"[{awc_var.array_size}]" if awc_var.array_size > 1 else ""
                    
                    # Instead of redefining, we inject a macro to redirect the generic name to the real name
                    # EXCEPT if decomp picked a good name already (like globalScalars2_x vs GlobalScalars2)
                    if awc_var.cbuffer_name.lower() not in v_name.lower():
                        macros.append(f"#define {awc_var.cbuffer_name} {v_name}")
                    
                    # Comment the existing line to show clarity
                    comment = var_match.group(6) or ""
                    clean_line = line.replace(comment, "").rstrip()
                    new_lines.append(f"{clean_line} // AWC: {awc_var.type_name} {awc_var.cbuffer_name}{array_str}")
                    continue
            
            new_lines.append(line)
        else:
            # Check for generic texture/sampler/uav bindings
            ts_match = re.match(r'^\s*([\w\d_<>]+)\s+([\w\d_]+)\s*:\s*register\(([tsu])(\d+)(?:,\s*space(\d+))?\);(.*)$', line)
            if ts_match:
                # var_type = ts_match.group(1)
                v_name = ts_match.group(2)
                prefix = ts_match.group(3)
                slot = ts_match.group(4)
                space = ts_match.group(5) if ts_match.group(5) else "0"
                
                key = (prefix, slot, space)
                if key in awc_srv_uav_reg:
                    true_name = awc_srv_uav_reg[key]
                    if true_name.lower() not in v_name.lower():
                        macros.append(f"#define {true_name} {v_name}")
                    
                    comment = ts_match.group(6) or ""
                    clean_line = line.replace(comment, "").rstrip()
                    new_lines.append(f"{clean_line} // AWC: {true_name}")
                    continue
                    
            new_lines.append(line)
            
    # Find insertion point for macros (after Original Entry Function comments)
    insert_pos = 0
    for i, line in enumerate(new_lines):
        if line.strip() and not line.strip().startswith('//'):
            insert_pos = i
            break
            
    macros.append("")
    final_lines = new_lines[:insert_pos] + macros + new_lines[insert_pos:]
    
    with open(hlsl_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(final_lines))
        
    return True, f"Injected AWC Variables and Aliases successfully."

def annotate_directory(hlsl_dir: str, awc: AWCFile) -> Dict[str, str]:
    """
    Annotate all HLSL files in a directory.
    Returns dict of filename -> result message.
    """
    lookup = build_shader_lookup(awc)
    results = {}
    
    for filename in os.listdir(hlsl_dir):
        if filename.endswith('.hlsl'):
            filepath = os.path.join(hlsl_dir, filename)
            success, message = annotate_hlsl_file(filepath, lookup)
            results[filename] = f"{'[OK]' if success else '[SKIP]'} {message}"
    
    return results

if __name__ == '__main__':
    # Test with command line args
    import sys
    from parser import parse_awc_file
    
    if len(sys.argv) < 3:
        print("Usage: python exporter.py <awc_file> <hlsl_directory>")
        sys.exit(1)
    
    awc_path = sys.argv[1]
    hlsl_dir = sys.argv[2]
    
    print(f"Loading AWC: {awc_path}")
    awc = parse_awc_file(awc_path)
    print(f"Loaded {awc.total_shader_count} shaders")
    
    print(f"\\nAnnotating HLSL files in: {hlsl_dir}")
    results = annotate_directory(hlsl_dir, awc)
    
    for filename, msg in sorted(results.items()):
        print(f"  {filename}: {msg}")
