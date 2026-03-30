"""
Binary parser for AWC Shader Library files.
Implements parsing logic based on 010 Editor template.
"""

import struct
from typing import BinaryIO, List
from .models import AWCFile, Shader, Register, CBufferData


class AWCParser:
    """Parser for AWC shader library files."""
    
    def __init__(self, file: BinaryIO):
        self.file = file
        
    def read_byte(self) -> int:
        """Read a single unsigned byte."""
        return struct.unpack('B', self.file.read(1))[0]
    
    def read_ushort(self) -> int:
        """Read an unsigned short (2 bytes, little endian)."""
        return struct.unpack('<H', self.file.read(2))[0]
    
    def read_uint(self) -> int:
        """Read an unsigned int (4 bytes, little endian)."""
        return struct.unpack('<I', self.file.read(4))[0]
    
    def read_uint64(self) -> int:
        """Read an unsigned int64 (8 bytes, little endian)."""
        return struct.unpack('<Q', self.file.read(8))[0]
    
    def read_bytes(self, count: int) -> bytes:
        """Read specified number of bytes."""
        return self.file.read(count)
    
    def read_string(self) -> str:
        """Read a null-terminated string."""
        chars = []
        while True:
            c = self.file.read(1)
            if c == b'\x00' or not c:
                break
            chars.append(c.decode('latin-1'))
        return ''.join(chars)
    
    def read_fixed_string(self, length: int) -> str:
        """Read a fixed-length string."""
        data = self.file.read(length)
        return data.decode('latin-1', errors='replace').rstrip('\x00')
    
    def tell(self) -> int:
        """Get current file position (equivalent to FTell())."""
        return self.file.tell()
    
    def seek(self, pos: int):
        """Seek to position (equivalent to FSeek())."""
        self.file.seek(pos)
    
    def parse_cbuffer_data(self) -> CBufferData:
        """Parse a CBuffer data entry."""
        p_current = self.tell()
        
        type_val = self.read_ushort()
        array_size = self.read_ushort()
        pack_offset = self.read_ushort()
        cbuffer_name_offset = self.read_uint()
        
        # Read cbuffer name at offset
        self.seek(p_current + cbuffer_name_offset)
        cbuffer_name = self.read_string()
        
        # Return to read remaining bytes
        self.seek(p_current + 10)
        unk_bytes1 = self.read_bytes(14)
        
        return CBufferData(
            type=type_val,
            array_size=array_size,
            pack_offset=pack_offset,
            cbuffer_name_offset=cbuffer_name_offset,
            cbuffer_name=cbuffer_name,
            name_hash_data=unk_bytes1
        )
    
    def parse_register(self) -> Register:
        """Parse a register entry."""
        p_cbuffer_start = self.tell()
        
        resource_type = self.read_ushort()
        register_slot = self.read_ushort()
        cbuffer_count = self.read_byte()
        num_descriptors = self.read_byte()
        register_space = self.read_byte()
        reserved = self.read_byte()
        cbuffer_data_offset = self.read_ushort()
        reg_string_offset = self.read_ushort()
        
        p_current = self.tell()
        
        # Read 16 bytes of extra register data (cbuffer_size, name_hash, etc.)
        extra_data = self.read_bytes(16)
        
        # Read register name
        self.seek(p_current + reg_string_offset - 12)
        reg_name = self.read_string()
        
        # Determine valid cbuffer count
        cbuffer_valid_count = cbuffer_count if cbuffer_data_offset != 0 else 0
        
        # Parse cbuffers
        cbuffers = []
        if cbuffer_valid_count > 0:
            self.seek(p_current + cbuffer_data_offset - 12)
            for _ in range(cbuffer_valid_count):
                cbuffers.append(self.parse_cbuffer_data())
        
        # Move past the extra data area (16 bytes after the 12-byte header)
        self.seek(p_current + 16)
        
        return Register(
            resource_type=resource_type,
            register_slot=register_slot,
            cbuffer_count=cbuffer_count,
            num_descriptors=num_descriptors,
            register_space=register_space,
            reserved=reserved,
            cbuffer_data_offset=cbuffer_data_offset,
            reg_string_offset=reg_string_offset,
            reg_name=reg_name,
            extra_data=extra_data,
            cbuffers=cbuffers
        )
    
    def parse_shader(self) -> Shader:
        """Parse a shader structure."""
        # Read shader name (length-prefixed)
        slen = self.read_byte()
        name = self.read_fixed_string(slen)
        
        wavesize = self.read_byte()
        size = self.read_uint()
        shader_binary = self.read_bytes(size)
        hash_val = self.read_uint64()
        root_sig_data = self.read_bytes(144)
        block_size = self.read_uint()
        
        p_block_start = self.tell()
        
        reg_count = self.read_ushort()
        cbuffer_count = self.read_ushort()
        tex_count = self.read_ushort()
        block_size_copy = self.read_ushort()
        
        # Parse registers
        registers = []
        for _ in range(reg_count):
            registers.append(self.parse_register())
        
        # Seek to end of block
        self.seek(p_block_start + block_size)
        
        return Shader(
            name=name,
            wavesize=wavesize,
            size=size,
            shader_binary=shader_binary,
            hash=hash_val,
            root_sig_data=root_sig_data,
            block_size=block_size,
            reg_count=reg_count,
            cbuffer_count=cbuffer_count,
            tex_count=tex_count,
            block_size_copy=block_size_copy,
            registers=registers
        )
    
    def parse_shader_array(self) -> List[Shader]:
        """Parse an array of shaders (count followed by shaders)."""
        count = self.read_uint()
        shaders = []
        for _ in range(count):
            shaders.append(self.parse_shader())
        return shaders
    
    def parse(self) -> AWCFile:
        """Parse the complete AWC file."""
        # Read magic
        magic = self.read_fixed_string(4)
        
        # Parse all shader types
        vertex_shaders = self.parse_shader_array()
        pixel_shaders = self.parse_shader_array()
        geometry_shaders = self.parse_shader_array()
        domain_shaders = self.parse_shader_array()
        hull_shaders = self.parse_shader_array()
        compute_shaders = self.parse_shader_array()
        
        return AWCFile(
            magic=magic,
            vertex_shaders=vertex_shaders,
            pixel_shaders=pixel_shaders,
            geometry_shaders=geometry_shaders,
            domain_shaders=domain_shaders,
            hull_shaders=hull_shaders,
            compute_shaders=compute_shaders
        )


def parse_awc_file(filepath: str) -> AWCFile:
    """Parse an AWC file from the given path."""
    with open(filepath, 'rb') as f:
        parser = AWCParser(f)
        return parser.parse()
