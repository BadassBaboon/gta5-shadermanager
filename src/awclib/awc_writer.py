"""
AWC Writer module.
Writes AWC shader library files with modified shader binaries.
Supports full rebuild when shader sizes change.
"""

import struct
import subprocess
import tempfile
import configparser
from pathlib import Path
from typing import BinaryIO, List, Optional, Tuple, Dict
from .models import AWCFile, Shader, Register, CBufferData, ValueType, ResourceType
from .dxbc_parser import scan_shader


def _find_dxc(dxc_path=None):
    """Locate dxc.exe: explicit path, else settings.ini's dx12_compiler_path under the
    shadermanager root, else dxcompilers/dxc.exe. Returns Path or None."""
    if dxc_path:
        p = Path(dxc_path)
        if p.exists():
            return p
    root = Path(__file__).resolve().parent.parent  # shadermanager/
    rel = "dxcompilers/dxc.exe"
    try:
        cfg = configparser.ConfigParser()
        cfg.read(root / "settings.ini")
        rel = cfg.get("Paths", "dx12_compiler_path", fallback=rel)
    except Exception:
        pass
    p = root / rel
    return p if p.exists() else None


def _dxbc_chunks(blob: bytes):
    """Return the list of 4-byte chunk FourCCs in a DXBC/DXIL container."""
    if not blob or len(blob) < 32 or blob[:4] != b'DXBC':
        return []
    try:
        n = struct.unpack_from('<I', blob, 28)[0]
        return [blob[o:o + 4] for o in struct.unpack_from(f'<{n}I', blob, 32)]
    except Exception:
        return []


def transplant_rootsig(old_binary: bytes, new_binary: bytes, dxc) -> Tuple[Optional[bytes], str]:
    """Copy the ORIGINAL shader's embedded root signature (RTS0) onto a freshly compiled
    shader and re-sign it, so the injected shader keeps a PSO-valid root signature.

    Every stock GTA5 Enhanced shader embeds an RTS0 chunk; the game builds its PSOs from
    that signature. Recompiled HLSL emits no RTS0, so without this the injected shader fails
    PSO creation -> the game can crash or the effect silently never runs.

    Returns (patched_bytes, message):
      - (new_binary, 'no root signature in original') -- stock shader has no RTS0, inject as-is.
      - (patched_bytes, 'ok') -- success, RTS0 spliced in and re-signed.
      - (None, '<reason>') -- the original HAS a root sig but it can't be attached (the
        modified shader binds a resource the stock root sig lacks) OR dxc is unavailable.
        The caller MUST NOT inject in that case; doing so risks a GPU/PSO crash.
    """
    if b'RTS0' not in _dxbc_chunks(old_binary):
        return new_binary, 'no root signature in original'
    if dxc is None or not Path(dxc).exists():
        return None, 'dxc.exe not found -- cannot validate/preserve root signature'
    dxc = str(dxc)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / 'old.cso').write_bytes(old_binary)
        (td / 'new.cso').write_bytes(new_binary)
        r = subprocess.run([dxc, '-dumpbin', str(td / 'old.cso'),
                            '-extractrootsignature', '-Fo', str(td / 'rs.bin')],
                           capture_output=True, text=True)
        if r.returncode != 0 or not (td / 'rs.bin').exists() or (td / 'rs.bin').stat().st_size == 0:
            return None, 'failed to extract original root signature'
        r = subprocess.run([dxc, '-dumpbin', str(td / 'new.cso'),
                            '-setrootsignature', str(td / 'rs.bin'), '-Fo', str(td / 'final.cso')],
                           capture_output=True, text=True)
        if r.returncode != 0 or not (td / 'final.cso').exists():
            tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'setrootsignature failed'
            return None, f'root signature incompatible: {tail}'
        return (td / 'final.cso').read_bytes(), 'ok'


class AWCWriter:
    """Writer for AWC shader library files."""
    
    def __init__(self, file: BinaryIO):
        self.file = file
        
    def write_byte(self, value: int):
        """Write a single unsigned byte."""
        self.file.write(struct.pack('B', value))
    
    def write_ushort(self, value: int):
        """Write an unsigned short (2 bytes, little endian)."""
        self.file.write(struct.pack('<H', value))
    
    def write_uint(self, value: int):
        """Write an unsigned int (4 bytes, little endian)."""
        self.file.write(struct.pack('<I', value))
    
    def write_uint64(self, value: int):
        """Write an unsigned int64 (8 bytes, little endian)."""
        self.file.write(struct.pack('<Q', value))
    
    def write_bytes(self, data: bytes):
        """Write raw bytes."""
        self.file.write(data)
    
    def write_string(self, s: str):
        """Write a length-prefixed string."""
        encoded = s.encode('latin-1')
        self.write_byte(len(encoded))
        self.file.write(encoded)
    
    def tell(self) -> int:
        """Get current file position."""
        return self.file.tell()
    
    def seek(self, pos: int):
        """Seek to position."""
        self.file.seek(pos)


class AWCRebuilder:
    """
    Rebuilds an AWC file from parsed data structures.
    
    This approach preserves the original block data exactly as-is,
    only updating the shader binaries and their size fields.
    For modified shaders, it rebuilds the metadata block.
    """
    
    def __init__(self, awc: AWCFile, original_path: str):
        self.awc = awc
        self.original_path = original_path
        self.original_blocks = {}  # shader_name -> block_data
        self._extract_block_data()
    
    def _extract_block_data(self):
        """Extract the block data for each shader from the original file."""
        self.original_blocks = {
            'vertex': [], 'pixel': [], 'geometry': [], 
            'domain': [], 'hull': [], 'compute': []
        }
        
        type_keys = ['vertex', 'pixel', 'geometry', 'domain', 'hull', 'compute']
        
        try:
            with open(self.original_path, 'rb') as f:
                # Skip magic
                f.read(4)
                
                # Follow exact order of types
                for type_key in type_keys:
                    count = struct.unpack('<I', f.read(4))[0]
                    
                    for _ in range(count):
                        # Read shader header
                        slen = struct.unpack('B', f.read(1))[0]
                        name_bytes = f.read(slen)
                        # name = name_bytes.decode('latin-1').rstrip('\x00')
                        
                        wavesize = struct.unpack('B', f.read(1))[0]
                        size = struct.unpack('<I', f.read(4))[0]
                        
                        # Skip shader binary
                        f.read(size)
                        
                        # Read hash and range
                        hash_val = struct.unpack('<Q', f.read(8))[0]
                        root_sig_data = f.read(144)
                        
                        # Read block
                        block_size = struct.unpack('<I', f.read(4))[0]
                        block_start = f.tell()
                        block_data = f.read(block_size)
                        
                        # Store block data in list
                        self.original_blocks[type_key].append({
                            'slen': slen,
                            'name_bytes': name_bytes,
                            'wavesize': wavesize,
                            'hash': hash_val,
                            'root_sig_data': range_data,
                            'block_size': block_size,
                            'block_data': block_data,
                        })
                
                # Read remaining data (footer)
                self.footer_data = f.read()
        except FileNotFoundError:
            # Handle case where original file is missing (e.g. creating new)
            # Not fully supported yet but fails safe
            print("Warning: Original AWC file not found. Rebuild might fail for unmodified shaders.")
            pass
    
    def write(self, output_path: str):
        """Write the rebuilt AWC file."""
        with open(output_path, 'wb') as f:
            writer = AWCWriter(f)
            
            # Write magic
            writer.write_bytes(self.awc.magic.encode('latin-1'))
            
            # Write shader arrays using keys to match block storage
            type_keys = ['vertex', 'pixel', 'geometry', 'domain', 'hull', 'compute']
            shader_lists = [
                self.awc.vertex_shaders,
                self.awc.pixel_shaders,
                self.awc.geometry_shaders,
                self.awc.domain_shaders,
                self.awc.hull_shaders,
                self.awc.compute_shaders,
            ]
            
            for key, shader_list in zip(type_keys, shader_lists):
                writer.write_uint(len(shader_list))
                
                for i, shader in enumerate(shader_list):
                    self._write_shader(writer, shader, key, i)
            
            # Write footer
            if hasattr(self, 'footer_data'):
                writer.write_bytes(self.footer_data)
    
    def _write_shader(self, writer: AWCWriter, shader: Shader, type_key: str, index: int):
        """Write a single shader."""
        
        # Get original block data by index (if available)
        block_info = None
        if type_key in self.original_blocks and index < len(self.original_blocks[type_key]):
            block_info = self.original_blocks[type_key][index]
        
        # Determine if we need to rebuild metadata
        rebuild_metadata = shader.metadata_dirty or block_info is None
        
        # Prep Data
        name_bytes = shader.name.encode('latin-1') + b'\x00'
        slen = len(name_bytes)
        
        if block_info:
            # Use original name bytes/len to preserve padding if not modifying name
            # But currently we don't support renaming shaders easily
            slen = block_info['slen'] 
            name_bytes = block_info['name_bytes']
            hash_val = block_info['hash']
            range_data = block_info['root_sig_data']
            # If we rebuilt, we still use original header info where possible?
            # Yes, hash/range usually unchanged by simple code edit? 
            # Actually hash should probably update but we don't know algorithm.
        else:
            # Fallback for new shader
            hash_val = shader.hash
            range_data = shader.root_sig_data
        
        # Write Name
        writer.write_byte(slen)
        writer.write_bytes(name_bytes)
        
        # Write Wavesize
        writer.write_byte(shader.wavesize)
        
        # Write Size
        writer.write_uint(len(shader.shader_binary))
        
        # Write Shader Binary
        writer.write_bytes(shader.shader_binary)
        
        # Write Hash
        writer.write_uint64(hash_val)
        
        # Write Range Data
        writer.write_bytes(range_data)
        
        # Prepare Block Data
        if rebuild_metadata:
             new_block_data = self._build_metadata_block(shader)
             block_size = len(new_block_data)
             block_data = new_block_data
        else:
             block_size = block_info['block_size']
             block_data = block_info['block_data']
             
        # Write Block Size
        writer.write_uint(block_size)
        
        # Write Block Data
        writer.write_bytes(block_data)

    def _build_metadata_block(self, shader: Shader) -> bytes:
        """
        Rebuilds the shader metadata block (Registers, CBuffers, etc.)
        from the Shader object model.
        """
        # Header: reg_count(2), cbuffer_count(2), tex_count(2), block_size_dup(2)
        # However, block_size_dup usually == block_size? 
        # In parser, it reads block_size, then reads header.
        # Actually block_size_dup is the last field of header usually.
        # Let's verify parser:
        # p_block_start = tell()
        # reg_cnt, cb_cnt, tex_cnt, blk_dup = read 8 bytes.
        # So yes.
        
        reg_count = len(shader.registers)
        cbuffer_count = sum(len(reg.cbuffers) for reg in shader.registers)
        tex_count = shader.tex_count # We don't track textures in model yet? 
        # Wait, Shader model has tex_count field.
        # If we parsed it, we have it. If we didn't update it, it's old count.
        # For now, keep existing tex_count unless we add texture parsing.
        
        # Calculate block size relative to block start
        # but we are building the buffer content.
        
        buffer = bytearray()
        
        # placeholder for header (8 bytes)
        # We need to fill block_size_dup later if it needs to match block_size logic
        # Usually block_size_dup matches the size of this block?
        # Let's assume yes.
        
        buffer.extend(struct.pack('<H', reg_count))
        buffer.extend(struct.pack('<H', cbuffer_count))
        buffer.extend(struct.pack('<H', tex_count))
        buffer.extend(struct.pack('<H', 0)) # block_size_copy placeholder
        
        header_size = 8
        reg_headers_start = header_size
        reg_headers_size = reg_count * 28  # 12 bytes header + 16 bytes extra per register
        
        # Reserve space for register headers (filled in later)
        buffer.extend(b'\x00' * reg_headers_size)
        
        # Pass 1: Write all cbuffer structs for all registers (in order)
        # Track where each register's cbuffer data starts
        cb_struct_positions = []  # (p_reg, cb_structs_pos, cbuffer_list)
        
        for i, reg in enumerate(shader.registers):
            p_reg = reg_headers_start + (i * 28)
            cbuffer_list = reg.cbuffers
            
            if cbuffer_list:
                cb_structs_pos = len(buffer)
                cb_struct_positions.append((p_reg, cb_structs_pos, cbuffer_list))
                # Reserve space for CBuffer structs (24 bytes each)
                buffer.extend(b'\x00' * (len(cbuffer_list) * 24))
            else:
                cb_struct_positions.append((p_reg, 0, []))
        
        # Pass 2: Write all register name strings, then all cbuffer name strings
        # First, register names
        reg_string_positions = []  # (p_reg, str_pos)
        for i, reg in enumerate(shader.registers):
            p_reg = reg_headers_start + (i * 28)
            str_pos = len(buffer)
            reg_string_positions.append((p_reg, str_pos))
            buffer.extend(reg.reg_name.encode('latin-1') + b'\x00')
        
        # Then, cbuffer variable names (and fill in cbuffer structs)
        for i, (p_reg, cb_structs_pos, cbuffer_list) in enumerate(cb_struct_positions):
            if not cbuffer_list:
                continue
                
            for j, cb in enumerate(cbuffer_list):
                p_cb = cb_structs_pos + (j * 24)
                
                # Write CBuffer variable name
                cb_str_pos = len(buffer)
                buffer.extend(cb.cbuffer_name.encode('latin-1') + b'\x00')
                
                cb_name_offset = cb_str_pos - p_cb
                
                # Write CBuffer Struct at p_cb
                # type(2), array_size(2), pack_offset(2), name_offset(4) = 10 bytes
                struct_data = struct.pack(
                    '<HHHI',
                    cb.type,
                    cb.array_size,
                    cb.pack_offset,
                    cb_name_offset
                )
                buffer[p_cb:p_cb+10] = struct_data
                
                # Write name_hash_data (14 bytes)
                hash_data = cb.name_hash_data
                if len(hash_data) != 14:
                    hash_data = b'\x00' * 14
                buffer[p_cb+10:p_cb+24] = hash_data
        
        # Pass 3: Fill in register headers
        for i, reg in enumerate(shader.registers):
            p_reg = reg_headers_start + (i * 28)
            _, cb_structs_pos, cbuffer_list = cb_struct_positions[i]
            _, str_pos = reg_string_positions[i]
            
            # Offset calculation: parser does seek(p_current + offset - 12)
            # where p_current = p_reg + 12 (after reading 12-byte header)
            # So: (p_reg + 12) + offset - 12 = target  =>  offset = target - p_reg
            cbuffer_data_offset = (cb_structs_pos - p_reg) if cbuffer_list else 0
            reg_string_offset = str_pos - p_reg
            
            reg_header = struct.pack(
                '<HHBBBBHH',
                reg.resource_type,
                reg.register_slot,
                len(cbuffer_list),
                reg.num_descriptors,
                reg.register_space,
                reg.reserved,
                cbuffer_data_offset,
                reg_string_offset
            )
            buffer[p_reg:p_reg+12] = reg_header
            
            # Write 16 bytes of extra data (cbuffer_size, name_hash, etc.)
            extra = reg.extra_data if hasattr(reg, 'extra_data') and len(reg.extra_data) == 16 else b'\x00' * 16
            buffer[p_reg+12:p_reg+28] = extra
        
        # Align to even byte count (some blocks have trailing padding)
        if len(buffer) % 2 != 0:
            buffer.append(0)
        
        # Update block_size_copy
        total_size = len(buffer)
        buffer[6:8] = struct.pack('<H', total_size)
        
        return bytes(buffer)


def rebuild_awc(awc: AWCFile, original_path: str, output_path: str):
    """
    Rebuild an AWC file with modified shader binaries.
    
    Args:
        awc: The AWCFile object with potentially modified shader binaries
        original_path: Path to original AWC file (for block data)
        output_path: Output file path
    """
    rebuilder = AWCRebuilder(awc, original_path)
    rebuilder.write(output_path)


def import_shader(awc: AWCFile, shader_type: str, shader_index: int,
                  cso_path: str, update_metadata: bool = False,
                  preserve_rootsig: bool = True, dxc_path: str = None) -> Tuple[bool, str]:
    """
    Import a CSO file as a shader replacement.
    If update_metadata is True, auto-detects resources and updates metadata.
    If False (default), only replaces the shader binary.

    preserve_rootsig (default True): splice the ORIGINAL slot's embedded root signature
    onto the new bytecode and re-sign (see transplant_rootsig). If the new shader can't
    take the stock root signature, the import is REFUSED (returns False) and the slot is
    left unchanged -- this prevents injecting a shader that crashes the game at PSO
    creation. Set False only for deliberate raw swaps. dxc_path overrides dxc autodetect.
    """
    shader_lists = {
        'vertex': awc.vertex_shaders,
        'pixel': awc.pixel_shaders,
        'geometry': awc.geometry_shaders,
        'domain': awc.domain_shaders,
        'hull': awc.hull_shaders,
        'compute': awc.compute_shaders,
    }
    
    shader_list = shader_lists.get(shader_type)
    if not shader_list:
        return False, f"Invalid shader type: {shader_type}"
    
    if shader_index >= len(shader_list):
        return False, f"Shader index {shader_index} out of range"
    
    # Read CSO file
    try:
        with open(cso_path, 'rb') as f:
            new_binary = f.read()
    except Exception as e:
        return False, f"Failed to read CSO: {e}"
    
    # Validate DXBC/DXIL header
    if len(new_binary) < 4:
        return False, "CSO file too small"
    
    # Simple check for DXBC or DXIL
    magic = new_binary[:4]
    if magic != b'DXBC' and magic != b'DXIL':
        # Some variation might exist, but usually these start with magic.
        # Warning but proceed?
        pass 
    
    shader = shader_list[shader_index]
    old_size = shader.size

    # --- ROOT SIGNATURE PRESERVATION (prevents PSO-creation crashes) ---
    # shader.shader_binary here is still the ORIGINAL stock blob (we haven't swapped yet).
    # Splice its RTS0 onto the new bytecode; if that fails the new shader is incompatible
    # with the stock root signature, so refuse the import and leave the slot stock.
    if preserve_rootsig:
        patched, rs_msg = transplant_rootsig(
            shader.shader_binary, new_binary, _find_dxc(dxc_path))
        if patched is None:
            return False, (f"Root signature not preserved ({rs_msg}); "
                           f"slot left unchanged to avoid a GPU crash")
        new_binary = patched

    # Update shader binary
    shader.shader_binary = new_binary
    shader.size = len(new_binary)
    
    # --- METADATA REBUILDING (optional) ---
    if not update_metadata:
        msg_extra = " (Metadata Unchanged)"
        size_diff = shader.size - old_size
        size_msg = f" (size: {old_size} -> {shader.size}, diff: {size_diff:+d})"
        return True, f"Imported shader: {shader.name}{size_msg}{msg_extra}"
    
    try:
        resources = scan_shader(cso_path)
        
        # Convert resources to Register/CBufferData models
        new_registers = []
        
        for res in resources:
            if res.type == 'cbuffer':
                # Map variables to CBufferData
                cb_data_list = []
                # NOTE: ResourceDef has 'variables', but AWC Register has 'cbuffers' list.
                # Usually 1 Resource = 1 Register Slot with 1 CBufferData.
                # But if arrays?
                # For now, create one CBufferData per ResourceDef
                
                # Conversion logic
                # Map standard global registers to their canonical names
                reg_name = res.name
                if res.slot == 2 and res.space == 0:
                    reg_name = 'g_rage_matrices'
                elif res.slot == 5 and res.space == 0:
                     reg_name = 'misc_globals'
                elif res.slot == 6 and res.space == 0:
                     reg_name = 'lighting_globals'
                elif res.slot == 4 and res.space == 0:
                     reg_name = 'g_rage_clipplanes'
                elif res.slot == 9 and res.space == 1:
                     reg_name = 'im_cbuffer' # Common for Pixel Shaders
                
                # We need to map variable names? No, the CBuffer itself has a name.
                # AWC CBuffers have sub-variables?
                # No, AWC CBufferData represents the buffer ITSELF, not variables inside.
                # Wait, models.py: CBufferData has 'cbuffer_name'.
                # Does it have fields? No.
                # So AWC only tracks the Buffer object, not variables inside it?
                # Verify models.py: `CBufferData` has `type`, `array_size`, `pack_offset`.
                # This suggests CBufferData IS a variable/resource?
                # "CBufferData entry within a register"
                # If HLSL: `cbuffer A { float4 v; }`
                # Is AWC `CBufferData` corresponding to `A` or `v`?
                # Let's check `test_dxbc` output on the existing file.
                # `cb5` (Size 240) -> parser output 3 resources.
                # `cb5` is a cbuffer.
                # AWC parser `parse_cbuffer_data` reads "cbuffer name".
                # If AWC tracks individual variables, it would have many entries.
                # If it tracks the block, it has one.
                # Let's check `test_dxbc.py` output again vs `parser.py` logic.
                
                # AWC parser calls it `parse_cbuffer_data`.
                # In `injector.py`, it generates `float4 name : packoffset(...)`.
                # This strongly suggests `CBufferData` in AWC corresponds to a *VARIABLE* inside the CBuffer, OR the CBuffer itself if it's treated as one blob?
                # `injector.py`: `generate_cbuffer_body` iterates `sorted_cbs`.
                # `c_index = cb.pack_offset // 16`.
                # This confirms `CBufferData` = A VARIABLE (Uniform).
                # The `Register` corresponds to the `cbuffer {}` block (b-slot).
                # The `cbuffers` list in `Register` corresponds to the VARIABLES inside that block.
                
                # ALGORITHM CORRECTION:
                # 1 Resource (cbuffer) -> 1 Register object.
                # Resource variables -> Register.cbuffers list (CBufferData objects).
                
                for var in res.variables:
                    # Create CBufferData for this variable
                    # We need to map variable type to ValueType
                    v_type = ValueType.from_string(var.type)
                    
                    # --- MATRIX FUSION LOGIC ---
                    # If parser detected Float4[4] but name suggests matrix, force Float4x4
                    # This fixes game crashes where it expects specific matrix types
                    
                    array_size = 1 # Default
                    # Parse array from name if needed (parser might have put it in name)
                    # No, parser logic puts it in 'size'.
                    
                    # Our parser puts total size in var.size.
                    # float4 is 16 bytes. float4[4] is 64 bytes.
                    
                    is_matrix_candidate = False
                    name_lower = var.name.lower()
                    if 'matrix' in name_lower or 'matrices' in name_lower or 'world' in name_lower or 'proj' in name_lower:
                        is_matrix_candidate = True
                        
                    if is_matrix_candidate:
                        if v_type == ValueType.Float4:
                            if var.size == 64:
                                # 64 bytes = 4 vectors = 1 matrix
                                v_type = ValueType.Float4x4
                                array_size = 1
                            elif var.size > 64 and (var.size % 64 == 0):
                                # Array of matrices
                                v_type = ValueType.Float4x4
                                array_size = var.size // 64
                            elif var.size == 48:
                                 # 48 bytes = float4x3
                                 v_type = ValueType.Float4x3
                                 array_size = 1

                        # --- RENAME LOGIC ---
                        # Map generic parser names to canonical Rage Engine names
                        matrix_map = {
                            0x0 : 'gWorld',
                            0x40 : 'gWorldView',
                            0x80 : 'gWorldViewProj',
                            0xC0 : 'gViewInverse',
                            0x100 : 'gView',
                            0x140 : 'gRelativeWorldView',
                            0x180 : 'gRelativeWorldViewProj',
                            0x1C0 : 'gPrevWorld',
                            0x200 : 'gPrevWorldView',
                            0x240 : 'gPrevWorldViewProj',
                            0x280 : 'gPrevViewProj',
                            0x2C0 : 'gRelativePrevWorld',
                            0x300 : 'gRelativePrevWorldView',
                            0x340 : 'gRelativePrevWorldViewProj'
                        }
                        
                        if var.offset in matrix_map:
                            var.name = matrix_map[var.offset]
                    
                    # Create object
                    cb_data = CBufferData(
                        type=v_type,
                        array_size=array_size, 
                        pack_offset=var.offset, # Assuming byte offset
                        cbuffer_name_offset=0, # Calculated on write
                        cbuffer_name=var.name,
                        name_hash_data=b'\x00' * 14 # Default
                    )
                    cb_data_list.append(cb_data)
                

                if reg_name == 'g_rage_matrices':
                     # Gap Filling Logic for Matrices
                     standard_rage_matrices = {
                        0x0: ('gWorld', ValueType.Float4x4),
                        0x40: ('gWorldView', ValueType.Float4x4),
                        0x80: ('gWorldViewProj', ValueType.Float4x4),
                        0xC0: ('gViewInverse', ValueType.Float4x4),
                        0x100: ('gView', ValueType.Float4x4),
                        0x140: ('gRelativeWorldView', ValueType.Float4x4),
                        0x180: ('gRelativeWorldViewProj', ValueType.Float4x4),
                        0x1C0: ('gPrevWorld', ValueType.Float4x4),
                        0x200: ('gPrevWorldView', ValueType.Float4x4),
                        0x240: ('gPrevWorldViewProj', ValueType.Float4x4),
                        0x280: ('gPrevViewProj', ValueType.Float4x4),
                        0x2C0: ('gRelativePrevWorld', ValueType.Float4x4),
                        0x300: ('gRelativePrevWorldView', ValueType.Float4x4),
                        0x340: ('gRelativePrevWorldViewProj', ValueType.Float4x4),
                    }
                    
                     existing_offsets = {cb.pack_offset for cb in cb_data_list}
                    
                     for offset, (name, v_type) in standard_rage_matrices.items():
                         if offset not in existing_offsets:
                             # Inject missing matrix
                             new_cb = CBufferData(
                                 type=v_type, array_size=1, pack_offset=offset,
                                 cbuffer_name_offset=0, cbuffer_name=name, name_hash_data=b'\x00' * 14
                             )
                             cb_data_list.append(new_cb)
                             
                elif reg_name == 'g_rage_clipplanes':
                     standard_clipplanes = {
                         # Clip planes are usually 6 float4s
                         0x0: ('gClipPlanes', ValueType.Float4, 6), # Array of 6
                         # Some shaders might have 8? Standard seems to be 6 or 8.
                         # Based on usage, 6 is safe. If scan showed 6, use 6.
                         # Actually, let's inject individual planes if needed or just the array.
                         # If it's an array, we can't easily inject "part" of it.
                         # We'll assume if it's missing, we inject the whole array.
                         0x0: ('gClipPlanes', ValueType.Float4), # Array check handled below
                     }
                     
                     # Check if gClipPlanes exists. If not, add it.
                     has_clipplanes = False
                     for cb in cb_data_list:
                         if 'clipplanes' in cb.cbuffer_name.lower():
                             has_clipplanes = True
                             break
                     
                     if not has_clipplanes:
                          new_cb = CBufferData(
                                 type=ValueType.Float4, array_size=6, pack_offset=0x0,
                                 cbuffer_name_offset=0, cbuffer_name='gClipPlanes', name_hash_data=b'\x00' * 14
                             )
                          cb_data_list.append(new_cb)

                elif reg_name == 'misc_globals':
                    # Standard misc globals layout based on scan
                    standard_misc = [
                         (0x0, 'globalRotatableReflDir', ValueType.Float4),
                         (0x10, 'globalRotatableReflDirPrev', ValueType.Float4),
                         (0x20, 'globalScreenSize', ValueType.Float4),
                         (0x30, 'globalSceneDepthInfo', ValueType.Float4),
                         (0x40, 'globalScreenSpacePixelSize', ValueType.Float4),
                         (0x50, 'globalScreenSpacePixelSizeC', ValueType.Float4),
                         (0x60, 'globalUseHalfResolution', ValueType.Float),
                         (0x70, 'globalWorldToShadow', ValueType.Float4x4), # Matrix?
                         # ... many others ...
                         # We inject critical ones at start to preserve alignment
                    ]
                    
                    existing_offsets = {cb.pack_offset for cb in cb_data_list}
                    for offset, name, v_type in standard_misc:
                         if offset not in existing_offsets:
                             # For misc globals, explicit injection of knowns
                             new_cb = CBufferData(
                                 type=v_type, array_size=1, pack_offset=offset,
                                 cbuffer_name_offset=0, cbuffer_name=name, name_hash_data=b'\x00' * 14
                             )
                             cb_data_list.append(new_cb)
                             
                elif reg_name == 'lighting_globals':
                     # ... existing lighting logic ...
                     existing_offsets = {cb.pack_offset for cb in cb_data_list}
                     known_layout = [
                        (0x0, 'gNumForwardLights', ValueType.Uint, 1),
                        (0x30, 'gLightPositionAndInvDistSqr', ValueType.Float4, 8),
                        (0xB0, 'gLightDirectionAndFalloffExponent', ValueType.Float4, 8),
                        (0x130, 'gLightColourAndCapsuleExtent', ValueType.Float4, 8),
                        (0x1B0, 'gLightConeScale', ValueType.Float4, 8),
                        (0x230, 'gLightConeOffset', ValueType.Float4, 8),
                     ]
                     
                     for offset, name, v_type, arr_size in known_layout:
                         if offset not in existing_offsets:
                             new_cb = CBufferData(
                                 type=v_type, array_size=arr_size, pack_offset=offset,
                                 cbuffer_name_offset=0, cbuffer_name=name, name_hash_data=b'\x00' * 14
                             )
                             cb_data_list.append(new_cb)

                # Re-sort for all
                cb_data_list.sort(key=lambda x: x.pack_offset)

                reg = Register(
                    resource_type=ResourceType.ConstantBuffer,
                    register_slot=res.slot,
                    cbuffer_count=len(cb_data_list),
                    num_descriptors=1,
                    register_space=res.space,
                    reserved=0,
                    cbuffer_data_offset=0, # Calculated on write
                    reg_string_offset=0, # Calculated on write
                    reg_name=reg_name,
                    cbuffers=cb_data_list
                )
                new_registers.append(reg)
            
            elif res.type == 'texture':
                # TODO: Texture handling. AWC might track textures in separate list or registers?
                # Shader model has `tex_count`.
                # Currently we only fixed CBuffer crash. Textures might be needed too.
                # But typically crash comes from CBuffer size mismatch.
                pass
                
        # MERGE: keep all original registers, only ADD new ones
        # Build set of existing (slot, space) pairs
        existing_keys = {(r.register_slot, r.register_space) for r in shader.registers}
        
        added_count = 0
        for new_reg in new_registers:
            key = (new_reg.register_slot, new_reg.register_space)
            if key not in existing_keys:
                shader.registers.append(new_reg)
                existing_keys.add(key)
                added_count += 1
                print(f"  Added new register: {new_reg.reg_name} [b{new_reg.register_slot}, space{new_reg.register_space}]")
        
        if added_count > 0:
            # Sort registers by (space, slot) to match original AWC ordering
            shader.registers.sort(key=lambda r: (r.register_space, r.register_slot))
            shader.metadata_dirty = True
        else:
            # No new registers, metadata unchanged - don't rebuild
            shader.metadata_dirty = False
        
        msg_extra = " (Metadata Updated)"
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Metadata update failed: {e}")
        msg_extra = " (Metadata Update FAILED)"
    
    size_diff = shader.size - old_size
    size_msg = f" (size: {old_size} -> {shader.size}, diff: {size_diff:+d})"
    
    return True, f"Imported shader: {shader.name}{size_msg}{msg_extra}"


class AWCWriter:
    """Writer for AWC shader library files."""
    
    def __init__(self, file: BinaryIO):
        self.file = file
        
    def write_byte(self, value: int):
        """Write a single unsigned byte."""
        self.file.write(struct.pack('B', value))
    
    def write_ushort(self, value: int):
        """Write an unsigned short (2 bytes, little endian)."""
        self.file.write(struct.pack('<H', value))
    
    def write_uint(self, value: int):
        """Write an unsigned int (4 bytes, little endian)."""
        self.file.write(struct.pack('<I', value))
    
    def write_uint64(self, value: int):
        """Write an unsigned int64 (8 bytes, little endian)."""
        self.file.write(struct.pack('<Q', value))
    
    def write_bytes(self, data: bytes):
        """Write raw bytes."""
        self.file.write(data)
    
    def write_string(self, s: str):
        """Write a length-prefixed string."""
        encoded = s.encode('latin-1')
        self.write_byte(len(encoded))
        self.file.write(encoded)
    
    def tell(self) -> int:
        """Get current file position."""
        return self.file.tell()
    
    def seek(self, pos: int):
        """Seek to position."""
        self.file.seek(pos)


class AWCRebuilder:
    """
    Rebuilds an AWC file from parsed data structures.
    
    This approach preserves the original block data exactly as-is,
    only updating the shader binaries and their size fields.
    We read the original file to extract block data that we don't
    fully understand yet.
    """
    
    def __init__(self, awc: AWCFile, original_path: str):
        self.awc = awc
        self.original_path = original_path
        self.original_blocks = {}  # shader_name -> block_data
        self._extract_block_data()
    
    def _extract_block_data(self):
        """Extract the block data for each shader from the original file."""
        self.original_blocks = {
            'vertex': [], 'pixel': [], 'geometry': [], 
            'domain': [], 'hull': [], 'compute': []
        }
        
        type_keys = ['vertex', 'pixel', 'geometry', 'domain', 'hull', 'compute']
        
        with open(self.original_path, 'rb') as f:
            # Skip magic
            f.read(4)
            
            # Follow exact order of types
            for type_key in type_keys:
                count = struct.unpack('<I', f.read(4))[0]
                
                for _ in range(count):
                    # Read shader header
                    slen = struct.unpack('B', f.read(1))[0]
                    name_bytes = f.read(slen)
                    # name = name_bytes.decode('latin-1').rstrip('\x00')
                    
                    wavesize = struct.unpack('B', f.read(1))[0]
                    size = struct.unpack('<I', f.read(4))[0]
                    
                    # Skip shader binary
                    f.read(size)
                    
                    # Read hash and root signature data
                    hash_val = struct.unpack('<Q', f.read(8))[0]
                    root_sig_data = f.read(144)
                    
                    # Read block
                    block_size = struct.unpack('<I', f.read(4))[0]
                    block_start = f.tell()
                    block_data = f.read(block_size)
                    
                    # Store block data in list
                    self.original_blocks[type_key].append({
                        'slen': slen,
                        'name_bytes': name_bytes,
                        'wavesize': wavesize,
                        'hash': hash_val,
                        'root_sig_data': root_sig_data,
                        'block_size': block_size,
                        'block_data': block_data,
                    })
            
            # Read remaining data (footer)
            self.footer_data = f.read()
    
    def write(self, output_path: str):
        """Write the rebuilt AWC file."""
        with open(output_path, 'wb') as f:
            writer = AWCWriter(f)
            
            # Write magic
            writer.write_bytes(self.awc.magic.encode('latin-1'))
            
            # Write shader arrays using keys to match block storage
            type_keys = ['vertex', 'pixel', 'geometry', 'domain', 'hull', 'compute']
            shader_lists = [
                self.awc.vertex_shaders,
                self.awc.pixel_shaders,
                self.awc.geometry_shaders,
                self.awc.domain_shaders,
                self.awc.hull_shaders,
                self.awc.compute_shaders,
            ]
            
            for key, shader_list in zip(type_keys, shader_lists):
                writer.write_uint(len(shader_list))
                
                for i, shader in enumerate(shader_list):
                    self._write_shader(writer, shader, key, i)
            
            # Write footer
            if hasattr(self, 'footer_data'):
                writer.write_bytes(self.footer_data)
    
    def _write_shader(self, writer: AWCWriter, shader: Shader, type_key: str, index: int):
        """Write a single shader."""
        # Get original block data by index
        if type_key not in self.original_blocks or index >= len(self.original_blocks[type_key]):
             raise ValueError(f"Block data missing for {type_key} shader #{index}")
             
        block_info = self.original_blocks[type_key][index]
        
        # Write name (original bytes to preserve padding)
        writer.write_byte(block_info['slen'])
        writer.write_bytes(block_info['name_bytes'])
        
        # Write wavesize
        writer.write_byte(block_info['wavesize'])
        
        # Write size (potentially updated)
        writer.write_uint(len(shader.shader_binary))
        
        # Write shader binary (potentially modified)
        writer.write_bytes(shader.shader_binary)
        
        # Write hash (keep original - this might need recalculation)
        writer.write_uint64(block_info['hash'])
        
        # Write root signature data (keep original)
        writer.write_bytes(block_info['root_sig_data'])
        
        # Check if metadata was rebuilt (e.g. after CSO import with new bindings)
        if shader.metadata_dirty:
            # Use the first AWCRebuilder to build a new metadata block from the model
            from awclib.awc_writer import AWCRebuilder as _Rebuilder1
            # Get the first AWCRebuilder class (line 54) — but we can just use build_metadata_block directly
            # Since both classes share the same name, use the static method approach
            block_data = self._build_metadata_block(shader)
            writer.write_uint(len(block_data))
            writer.write_bytes(block_data)
        else:
            # Write original block data unchanged
            writer.write_uint(block_info['block_size'])
            writer.write_bytes(block_info['block_data'])
    
    def _build_metadata_block(self, shader: Shader) -> bytes:
        """Build a metadata block from the shader's register/cbuffer data.
        Builds metadata block for modified shaders during save."""
        import struct
        
        reg_count = len(shader.registers)
        cbuffer_count = sum(len(reg.cbuffers) for reg in shader.registers)
        tex_count = shader.tex_count if hasattr(shader, 'tex_count') else 0
        
        buffer = bytearray()
        
        buffer.extend(struct.pack('<H', reg_count))
        buffer.extend(struct.pack('<H', cbuffer_count))
        buffer.extend(struct.pack('<H', tex_count))
        buffer.extend(struct.pack('<H', 0))  # block_size_copy placeholder
        
        header_size = 8
        reg_headers_start = header_size
        reg_headers_size = reg_count * 28  # 12 bytes header + 16 bytes extra
        
        # Reserve space for register headers
        buffer.extend(b'\x00' * reg_headers_size)
        
        # Pass 1: Write all cbuffer structs for all registers
        cb_struct_positions = []
        for i, reg in enumerate(shader.registers):
            p_reg = reg_headers_start + (i * 28)
            cbuffer_list = reg.cbuffers
            if cbuffer_list:
                cb_structs_pos = len(buffer)
                cb_struct_positions.append((p_reg, cb_structs_pos, cbuffer_list))
                buffer.extend(b'\x00' * (len(cbuffer_list) * 24))
            else:
                cb_struct_positions.append((p_reg, 0, []))
        
        # Pass 2: Write all register name strings, then cbuffer name strings
        reg_string_positions = []
        for i, reg in enumerate(shader.registers):
            p_reg = reg_headers_start + (i * 28)
            str_pos = len(buffer)
            reg_string_positions.append((p_reg, str_pos))
            buffer.extend(reg.reg_name.encode('latin-1') + b'\x00')
        
        # Cbuffer variable names (and fill in cbuffer structs)
        for i, (p_reg, cb_structs_pos, cbuffer_list) in enumerate(cb_struct_positions):
            if not cbuffer_list:
                continue
            for j, cb in enumerate(cbuffer_list):
                p_cb = cb_structs_pos + (j * 24)
                cb_str_pos = len(buffer)
                buffer.extend(cb.cbuffer_name.encode('latin-1') + b'\x00')
                cb_name_offset = cb_str_pos - p_cb
                struct_data = struct.pack('<HHHI', cb.type, cb.array_size, cb.pack_offset, cb_name_offset)
                buffer[p_cb:p_cb+10] = struct_data
                hash_data = cb.name_hash_data
                if len(hash_data) != 14:
                    hash_data = b'\x00' * 14
                buffer[p_cb+10:p_cb+24] = hash_data
        
        # Pass 3: Fill in register headers
        for i, reg in enumerate(shader.registers):
            p_reg = reg_headers_start + (i * 28)
            _, cb_structs_pos, cbuffer_list = cb_struct_positions[i]
            _, str_pos = reg_string_positions[i]
            cbuffer_data_offset = (cb_structs_pos - p_reg) if cbuffer_list else 0
            reg_string_offset = str_pos - p_reg
            reg_header = struct.pack(
                '<HHBBBBHH',
                reg.resource_type, reg.register_slot,
                len(cbuffer_list), reg.num_descriptors,
                reg.register_space, reg.reserved,
                cbuffer_data_offset, reg_string_offset
            )
            buffer[p_reg:p_reg+12] = reg_header
            extra = reg.extra_data if hasattr(reg, 'extra_data') and len(reg.extra_data) == 16 else b'\x00' * 16
            buffer[p_reg+12:p_reg+28] = extra
        
        # Align to even byte count
        if len(buffer) % 2 != 0:
            buffer.append(0)
        
        total_size = len(buffer)
        buffer[6:8] = struct.pack('<H', total_size)
        
        return bytes(buffer)



