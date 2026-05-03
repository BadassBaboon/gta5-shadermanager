"""
Data models for AWC (SGD2) Shader Library file structure.
Based on 010 Editor template for GTA 5 Enhanced shader files.
Fully decoded field names from binary analysis.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional


class ValueType(IntEnum):
    """Shader value types as defined in the template."""
    Bool = 0
    Uint = 1
    Uint2 = 2
    Uint3 = 3
    Uint4 = 4
    Int = 5
    Int2 = 6
    Int3 = 7
    Int4 = 8
    Float = 9
    Float2 = 10
    Float3 = 11
    Float4 = 12
    Float4x3 = 13
    Float4x4 = 14
    
    @classmethod
    def get_name(cls, value: int) -> str:
        """Get name for a value type, or 'Unknown' if not found."""
        try:
            return cls(value).name
        except ValueError:
            return f"Unknown({value})"
            
    @classmethod
    def from_string(cls, name: str) -> int:
        """Get type value from string name map."""
        mapping = {
            'bool': cls.Bool,
            'uint': cls.Uint,
            'uint2': cls.Uint2,
            'uint3': cls.Uint3,
            'uint4': cls.Uint4,
            'int': cls.Int,
            'int2': cls.Int2,
            'int3': cls.Int3,
            'int4': cls.Int4,
            'float': cls.Float,
            'float2': cls.Float2,
            'float3': cls.Float3,
            'float4': cls.Float4, 
            'float4x3': cls.Float4x3,
            'float4x4': cls.Float4x4,
            # Aliases
            'matrix': cls.Float4x4,
            'vector': cls.Float4
        }
        name_lower = name.lower()
        if name_lower in mapping:
            return mapping[name_lower]
        return cls.Float4 # Default fallback


class ResourceType(IntEnum):
    """D3D12 resource descriptor types encoded in the register type field."""
    # SRV types (Shader Resource Views) - "t" registers
    Texture2D           = 0x0102  # 258  - Texture2D SRV
    Texture2DArray      = 0x0142  # 322  - Texture2DArray SRV
    TextureCube         = 0x0202  # 514  - TextureCube SRV
    Texture3D           = 0x0302  # 770  - Texture3D SRV
    Buffer              = 0x0401  # 1025 - Buffer SRV
    StructuredBuffer    = 0x0405  # 1029 - StructuredBuffer SRV
    ByteAddressBuffer   = 0x0407  # 1031 - ByteAddressBuffer SRV
    
    # UAV types (Unordered Access Views) - "u" registers
    RWTexture2D              = 0x011C  # 284  - RWTexture2D UAV
    RWTexture2DArray         = 0x015C  # 348  - RWTexture2DArray UAV
    RWStructuredBufferAppend = 0x040E  # 1038 - RWStructuredBuffer (append) UAV
    RWStructuredBuffer       = 0x0414  # 1044 - RWStructuredBuffer UAV
    RWStructuredBufferConsume= 0x0416  # 1046 - RWStructuredBuffer (consume) UAV
    RWByteAddressBuffer      = 0x0418  # 1048 - RWByteAddressBuffer UAV
    
    # Sampler - "s" registers
    SamplerState        = 0x0423  # 1059 - SamplerState
    
    # CBV (Constant Buffer Views) - "b" registers
    ConstantBuffer      = 0x0430  # 1072 - ConstantBuffer (CBV)
    
    @classmethod
    def get_register_prefix(cls, value: int) -> str:
        """Get HLSL register prefix (b/t/s/u) for a resource type value."""
        if value == cls.ConstantBuffer:
            return "b"
        elif value == cls.SamplerState:
            return "s"
        elif value in (cls.Texture2D, cls.Texture2DArray, cls.TextureCube, 
                       cls.Texture3D, cls.Buffer, cls.StructuredBuffer, 
                       cls.ByteAddressBuffer):
            return "t"
        else:
            return "u"  # UAV types
    
    @classmethod
    def get_name(cls, value: int) -> str:
        """Get human-readable name for a resource type value."""
        try:
            return cls(value).name
        except ValueError:
            return f"Unknown(0x{value:04X})"


@dataclass
class CBufferData:
    """CBuffer variable entry within a register."""
    type: int  # ValueType
    array_size: int
    pack_offset: int
    cbuffer_name_offset: int
    cbuffer_name: str
    # 14 bytes: [0:6]=padding, [6:10]=name_hash (uint32), [10:14]=padding
    name_hash_data: bytes = field(default=b'\x00'*14)
    
    @property
    def type_name(self) -> str:
        return ValueType.get_name(self.type)
    
    @property
    def hlsl_type_name(self) -> str:
        """Get the HLSL-compatible type name (lowercase, e.g. float4, uint, int)."""
        name = self.type_name
        if name.startswith('Unknown'):
            return 'float4'  # Safe fallback
        return name.lower()
    
    @property
    def byte_size(self) -> int:
        """Get byte size of this variable's base type."""
        _sizes = {
            0: 4,    # Bool
            1: 4,    # Uint
            2: 8,    # Uint2
            3: 12,   # Uint3
            4: 16,   # Uint4
            5: 4,    # Int
            6: 8,    # Int2
            7: 12,   # Int3
            8: 16,   # Int4
            9: 4,    # Float
            10: 8,   # Float2
            11: 12,  # Float3
            12: 16,  # Float4
            13: 48,  # Float4x3
            14: 64,  # Float4x4
        }
        return _sizes.get(self.type, 16)
    
    @property
    def name_hash(self) -> int:
        """Get the uint32 variable name hash from bytes [6:10]."""
        import struct
        if len(self.name_hash_data) >= 10:
            return struct.unpack('<I', self.name_hash_data[6:10])[0]
        return 0


@dataclass
class Register:
    """Register entry within a shader (D3D12 resource binding)."""
    resource_type: int      # D3D12 descriptor type (see ResourceType enum)
    register_slot: int      # HLSL register slot number
    cbuffer_count: int      # Number of CBuffer variable entries
    num_descriptors: int    # Number of descriptors in this range (1/2/8/16/32=bindless)
    register_space: int     # HLSL register space
    reserved: int           # Always 0, unused padding
    cbuffer_data_offset: int
    reg_string_offset: int
    reg_name: str
    # 16 extra bytes after the 12-byte header:
    # padding(2) + cbuffer_size(2) + padding(4) + name_hash(4) + name_hash_dup(4)
    extra_data: bytes = field(default=b'\x00'*16)
    cbuffers: List[CBufferData] = field(default_factory=list)
    
    @property
    def register_prefix(self) -> str:
        """Get HLSL register prefix (b/t/s/u) based on resource type."""
        return ResourceType.get_register_prefix(self.resource_type)


@dataclass
class Shader:
    """Shader structure."""
    name: str
    wavesize: int           # Wave lane count preference (50=Wave32, 65/66=Wave64)
    size: int
    shader_binary: bytes
    hash: int               # Unique uint64 content hash per shader permutation
    root_sig_data: bytes    # 144 bytes - Root signature fingerprint/summary
    block_size: int
    reg_count: int
    cbuffer_count: int
    tex_count: int
    block_size_copy: int    # Redundant copy of block_size (always equal)
    registers: List[Register] = field(default_factory=list)
    metadata_dirty: bool = field(default=False)
    
    @property
    def shader_type(self) -> str:
        """Determine shader type from name (if identifiable)."""
        return "Shader"


@dataclass
class AWCFile:
    """Complete AWC (SGD2) file structure."""
    magic: str  # "SGD2" - Shader Group Data v2
    vertex_shaders: List[Shader] = field(default_factory=list)
    pixel_shaders: List[Shader] = field(default_factory=list)
    geometry_shaders: List[Shader] = field(default_factory=list)
    domain_shaders: List[Shader] = field(default_factory=list)
    hull_shaders: List[Shader] = field(default_factory=list)
    compute_shaders: List[Shader] = field(default_factory=list)
    
    @property
    def total_shader_count(self) -> int:
        return (len(self.vertex_shaders) + len(self.pixel_shaders) + 
                len(self.geometry_shaders) + len(self.domain_shaders) +
                len(self.hull_shaders) + len(self.compute_shaders))
