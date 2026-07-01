import struct
import re
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

@dataclass
class ResourceDef:
    name: str
    type: str # 'cbuffer', 'texture', 'sampler', 'uav'
    slot: int
    space: int
    size: int # for cbuffers
    variables: List['VariableDef']

@dataclass
class VariableDef:
    name: str
    type: str
    offset: int
    size: int

class ShaderParser:
    def __init__(self, data: bytes):
        self.data = data
        self.resources: List[ResourceDef] = []
        
    def parse_dxbc(self) -> bool:
        if self.data[:4] != b'DXBC':
            return False
            
        try:
            # checksum = self.data[4:20]
            # version = self.data[20:24]
            # file_length = struct.unpack('<I', self.data[24:28])[0]
            chunk_count = struct.unpack('<I', self.data[28:32])[0]
            
            chunk_offsets = []
            for i in range(chunk_count):
                offset = 32 + (i * 4)
                chunk_offsets.append(struct.unpack('<I', self.data[offset:offset+4])[0])
                
            for offset in chunk_offsets:
                self._parse_chunk(offset)
            
            return len(self.resources) > 0
        except Exception as e:
            print(f"DXBC Parse Error: {e}")
            return False
            
    def _parse_chunk(self, offset: int):
        chunk_magic = self.data[offset:offset+4]
        # chunk_size = struct.unpack('<I', self.data[offset+4:offset+8])[0]
        chunk_data_start = offset + 8
        
        if chunk_magic == b'RDEF':
            self._parse_rdef(chunk_data_start)

    def _parse_string(self, abs_offset: int) -> str:
        end = self.data.find(b'\x00', abs_offset)
        if end == -1:
            return ""
        return self.data[abs_offset:end].decode('latin-1')

    def _parse_rdef(self, base_offset: int):
        pos = base_offset
        cb_count = struct.unpack('<I', self.data[pos:pos+4])[0]
        pos += 4
        cb_offset = struct.unpack('<I', self.data[pos:pos+4])[0]
        pos += 4
        res_count = struct.unpack('<I', self.data[pos:pos+4])[0]
        pos += 4
        res_offset = struct.unpack('<I', self.data[pos:pos+4])[0]
        
        # Parse CBuffers
        if cb_count > 0:
            cb_abs_start = base_offset + cb_offset
            for i in range(cb_count):
                self._parse_cbuffer_dxbc(base_offset, cb_abs_start + (i * 24))

    def _parse_cbuffer_dxbc(self, base_offset: int, cb_ptr: int):
        name_offset = struct.unpack('<I', self.data[cb_ptr:cb_ptr+4])[0]
        name = self._parse_string(base_offset + name_offset)
        
        var_count = struct.unpack('<I', self.data[cb_ptr+4:cb_ptr+8])[0]
        var_offset = struct.unpack('<I', self.data[cb_ptr+8:cb_ptr+12])[0]
        size = struct.unpack('<I', self.data[cb_ptr+12:cb_ptr+16])[0]
        # flags = struct.unpack('<I', self.data[cb_ptr+16:cb_ptr+20])[0]
        type_val = struct.unpack('<I', self.data[cb_ptr+20:cb_ptr+24])[0] # 0=CBuffer, 1=TBuffer
        
        variables = []
        if var_count > 0:
            var_abs_start = base_offset + var_offset
            for i in range(var_count):
                v = self._parse_variable_dxbc(base_offset, var_abs_start + (i * 24))
                variables.append(v)
        
        # Determine slot? DXBC RDEF doesn't specify slot in RDEF CBuffer struct directly?
        # Actually it does, but wait. RDEF links variables to cbuffers.
        # But where is the bind point?
        # Ah, bind point is in the Resource Binding struct, not CBuffer struct.
        # So I need to parse Resources to find the bind point for this cbuffer name.
        pass # Storing logic requires mapping name to bind point later.

    def _parse_variable_dxbc(self, base_offset: int, var_ptr: int) -> VariableDef:
        name_offset = struct.unpack('<I', self.data[var_ptr:var_ptr+4])[0]
        name = self._parse_string(base_offset + name_offset)
        start_offset = struct.unpack('<I', self.data[var_ptr+4:var_ptr+8])[0]
        size = struct.unpack('<I', self.data[var_ptr+8:var_ptr+12])[0]
        return VariableDef(name, "unknown", start_offset, size)

    def parse_hlsl(self, hlsl_code: str) -> bool:
        """Parse resources from Decompiled HLSL."""
        # Regex for cbuffers
        # cbuffer Name : register(b5) { ... };
        # cbuffer Name : register(b5, space1) { ... };
        pattern = re.compile(r'cbuffer\s+(\w+)\s*:\s*register\s*\(\s*b(\d+)(?:\s*,\s*space(\d+))?\s*\)\s*\{(.*?)\};', re.DOTALL)
        
        matches = pattern.finditer(hlsl_code)
        found = False
        
        for match in matches:
            found = True
            name = match.group(1)
            slot = int(match.group(2))
            space = int(match.group(3)) if match.group(3) else 0
            body = match.group(4)
            
            variables = self._parse_cbuffer_body_hlsl(body)
            size = self._calc_cbuffer_size(variables)
            
            self.resources.append(ResourceDef(name, 'cbuffer', slot, space, size, variables))
            
        return found
        
    def _parse_cbuffer_body_hlsl(self, body: str) -> List[VariableDef]:
        # float4 gWorld[4] : packoffset(c000.x);
        # float globalScalars3_z : packoffset(c014.z);
        # type name[array] : packoffset...
        vars = []
        lines = body.split(';')
        for line in lines:
            line = line.strip()
            if not line: continue
            
            # Simple packoffset parsing
            pack_match = re.search(r'packoffset\((c\d+)(?:\.([xyzw]))?\)', line)
            if pack_match:
                # We can calculate offset from packoffset
                # c000 -> 0 bytes
                # c001 -> 16 bytes
                c_reg = pack_match.group(1) # c000
                reg_idx = int(c_reg[1:])
                
                comp = pack_match.group(2) # x,y,z,w
                comp_idx = 0
                if comp == 'y': comp_idx = 4
                elif comp == 'z': comp_idx = 8
                elif comp == 'w': comp_idx = 12
                
                offset = (reg_idx * 16) + comp_idx
                
                # Parse declaration part: "row_major float4x4 gWorld"
                decl_part = line.split(':')[0].strip()
                tokens = decl_part.split()
                
                # Filter modifiers
                modifiers = {'row_major', 'column_major', 'static', 'const', 'uniform', 'snorm', 'unorm'}
                filtered = [t for t in tokens if t not in modifiers]
                
                if len(filtered) >= 2:
                    type_name = filtered[0]
                    name_part = filtered[-1]
                    
                    # Handle array
                    array_count = 1
                    if '[' in name_part:
                        name = name_part.split('[')[0]
                        try:
                            # Extract array size [4]
                            array_match = re.search(r'\[(\d+)\]', name_part)
                            if array_match:
                                array_count = int(array_match.group(1))
                        except:
                            pass
                    else:
                        name = name_part
                        
                    # Calculate size
                    size = 16 # Default
                    type_lower = type_name.lower()
                    
                    if 'float4x4' in type_lower or 'matrix' == type_lower:
                        size = 64
                    elif 'float4x3' in type_lower:
                        size = 64 # Aligned to 4 vectors usually? Or 48? in CBs, rows are 16-byte aligned.
                        # float4x3 is 3 rows of float4? Or 4 rows of float3?
                        # In HLSL, float4x3 is usually 4 vectors if column major?
                        # Safe assumption for cbuffer packing: matrix columns/rows align to 16 bytes.
                        # 4x3 -> 4 vectors of 16 bytes (last component padding) -> 64 bytes.
                        size = 64 
                    elif 'float3x4' in type_lower:
                        size = 48 # 3 vectors of 16 bytes
                    elif 'float3x3' in type_lower:
                        size = 48 # 3 vectors
                    elif 'float4x2' in type_lower:
                        size = 32
                    elif 'float4' in type_lower: 
                        size = 16
                    elif 'float3' in type_lower:
                        size = 12
                    elif 'float2' in type_lower:
                        size = 8
                    elif 'float' == type_lower:
                        size = 4
                    elif 'uint4' in type_lower or 'int4' in type_lower:
                        size = 16
                    elif 'uint' in type_lower or 'int' in type_lower:
                        size = 4
                        
                    size *= array_count
                    
                    vars.append(VariableDef(name, type_name, offset, size))
        return vars
        
    def _calc_cbuffer_size(self, vars: List[VariableDef]) -> int:
        if not vars: return 0
        last = vars[-1]
        # Align to 16 bytes
        end = last.offset + last.size
        return (end + 15) & ~15


def scan_shader(binary_path: str, tools_dir: str = None) -> List[ResourceDef]:
    """
    scans a shader binary for resource definitions.
    First attempts DXBC parsing. If no resources found or not DXBC,
    attempts to decompile using decomp.exe and parse HLSL.
    """
    with open(binary_path, 'rb') as f:
        data = f.read()
        
    parser = ShaderParser(data)
    
    # Try DXBC first (native)
    if parser.parse_dxbc() and len(parser.resources) > 0:
        return parser.resources
        
    # Fallback: Decompile
    # We need decomp.exe
    if not tools_dir:
         # Try to locate tools dir relative to current file or use default
         # Based on user's structure: r:\CoreFXProject\_TOOLS\GTATOOLS\fxc\fxc-converter-v0.0.2\dxcompilers
         tools_dir = r"r:\CoreFXProject\_TOOLS\GTATOOLS\fxc\fxc-converter-v0.0.2\dxcompilers"
         
    decomp_exe = os.path.join(tools_dir, "decomp.exe")
    if not os.path.exists(decomp_exe):
        print(f"Warning: decomp.exe not found at {decomp_exe}, cannot decompile DXIL/CSO.")
        return []
        
    # Run decomp
    try:
        # Temp file for hlsl
        fd, hlsl_path = tempfile.mkstemp(suffix='.hlsl', text=True)
        os.close(fd)
        
        # subprocess.run
        cmd = [decomp_exe, binary_path, hlsl_path]
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        if res.returncode == 0 and os.path.exists(hlsl_path):
            with open(hlsl_path, 'r') as f:
                hlsl_content = f.read()
            parser.parse_hlsl(hlsl_content)
        else:
            print(f"Decomp error: {res.stderr}")
            
        # Clean up
        if os.path.exists(hlsl_path):
            os.remove(hlsl_path)
            
    except Exception as e:
        print(f"Decompilaton/Parsing failed: {e}")
        
    return parser.resources
