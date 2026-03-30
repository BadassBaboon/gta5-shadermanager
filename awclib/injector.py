"""
Injector module for AWC Shader Viewer.
Injects full cbuffer definitions from AWC metadata into decompiled HLSL code.
"""

import re
from typing import Dict, List, Tuple
from .models import Shader, Register, CBufferData

def generate_cbuffer_body(reg: Register) -> str:
    """
    Generate the body of a cbuffer struct with packoffsets.
    """
    lines = []
    
    # Sort cbuffers by offset to ensure clean output (though packoffset allows any order)
    sorted_cbs = sorted(reg.cbuffers, key=lambda x: x.pack_offset)
    
    for cb in sorted_cbs:
        # Calculate c-register index and component
        c_index = cb.pack_offset // 16
        comp_offset = cb.pack_offset % 16
        comp_idx = comp_offset // 4
        comp_char = ['x', 'y', 'z', 'w'][comp_idx]
        
        # Format: packoffset(c12.y)
        pack_str = f"packoffset(c{c_index:03d}.{comp_char})"
        
        # Type and Name
        type_name = cb.type_name
        
        # Handle arrays
        array_str = ""
        if cb.array_size > 1:
            array_str = f"[{cb.array_size}]"
            
        # Clean name (remove special chars if any, though usually clean)
        name = cb.cbuffer_name
        
        # Line: float4 name[array] : packoffset(...);
        # Map types to HLSL types if needed
        hlsl_type = type_name
        # Simple mapping
        type_lower = type_name.lower()
        if 'uint' in type_lower: hlsl_type = 'uint' + type_lower[4:]
        elif 'int' in type_lower: hlsl_type = 'int' + type_lower[3:]
        elif 'float' in type_lower: hlsl_type = 'float' + type_lower[5:]
        elif type_name == 'Unknown': hlsl_type = 'float4' # Fallback
        else: hlsl_type = type_lower
        
        # Handle float4x4 etc
        
        lines.append(f"    {hlsl_type} {name}{array_str} : {pack_str};")
        
    return "\n".join(lines)

def inject_cbuffers(hlsl_code: str, shader: Shader) -> str:
    """
    Replace existing cbuffer definitions in HLSL with full definitions from shader metadata.
    """
    new_code = hlsl_code
    
    for reg in shader.registers:
        if not reg.cbuffers:
            continue
            
        # Register slot (e.g. b5)
        slot = reg.register_slot
        space = reg.register_space
        
        # Regex to find cbuffer block
        # Matches: cbuffer Name : register(b5) { ... };
        pattern = rf'cbuffer\s+(\w+)\s*:\s*register\s*\(\s*b{slot}\s*(?:,\s*space{space})?\s*\)\s*{{.*?}};'
        
        matches = list(re.finditer(pattern, new_code, re.DOTALL))
        
        if matches:
            cbuffer_name = matches[0].group(1)
            original_block = matches[0].group(0)
            
            # Extract content inside {}
            content_match = re.search(r'\{(.*?)\};', original_block, re.DOTALL)
            if not content_match:
                continue
                
            original_content = content_match.group(1)
            
            # Append aliased definitions
            new_definitions = "\n    // --- Injected AWC Metadata (Aliased) ---\n"
            new_definitions += generate_cbuffer_body(reg)
            
            new_content = original_content + "\n" + new_definitions
            
            # Replace in block
            new_block = original_block.replace(original_content, new_content)
            
            # Replace in code
            new_code = new_code.replace(original_block, new_block)
            
    return new_code

def generate_metadata_text(shader: Shader) -> str:
    """
    Generate a text containing full cbuffer definitions for the shader.
    """
    lines = []
    lines.append(f"// Full CBuffer Definitions for {shader.name}")
    lines.append("// Auto-generated from AWC Metadata")
    lines.append("// Copy these definitions into your HLSL to access all variables.")
    lines.append("")
    
    for reg in shader.registers:
        if not reg.cbuffers:
            continue
            
        slot = reg.register_slot
        space = reg.register_space
        
        # We use a generic name since we don't know the exact one being used in the user's HLSL
        # Or we can use reg.reg_name (e.g. misc_globals)
        cbuffer_name = reg.reg_name.replace(' ', '_').replace('.', '_')
        if not cbuffer_name: cbuffer_name = f"cb{slot}"
            
        lines.append(f"// Register b{slot} (space{space})")
        lines.append(f"cbuffer {cbuffer_name} : register(b{slot}, space{space})")
        lines.append("{")
        
        body = generate_cbuffer_body(reg)
        lines.append(body)
        
        lines.append("};")
        lines.append("")
        
    return "\n".join(lines)
