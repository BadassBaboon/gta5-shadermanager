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
class PropBinding:
    """One (resource_name_hash, packed_tag) tuple inside a PropEntry.

    The hash is a 32-bit Jenkins One-At-A-Time (lowercase) joaat() of a
    resource name that appears in the effect's string pool — e.g. a sampler
    state, a texture/buffer SRV/UAV, a cbuffer, or a global parameter.
    The packed tag's exact bit layout is partially decoded:
       byte0/byte1 (u16) — D3D12 resource type marker (matches ResourceType)
       byte2 (u8)        — local slot/sequence index within the entry
       byte3 (u8)        — flags (always 0 in observed files)
    """
    name_hash: int      # joaat_lower(name) — 32-bit
    tag: int            # raw 32-bit tag
    name: str = ""      # resolved from string pool / shader register names (best-effort)


@dataclass
class PropEntry:
    """One entry in the 'proplst' table.

    Each entry groups a logical effect property (a parameter / sampler /
    cbuffer / output) together with one or more bindings. The 64-bit hash
    identifies the entry — empirically it is a content / signature hash
    computed from the binding set; we treat it as opaque.

    Layout on disk (size given by the preceding u32):
        +0x00  u64    entry_hash
        +0x08  u8     binding_count    (low byte of next u32)
        +0x09  3 B    reserved (zero in observed files)
        +0x0C  u32    flags2 (low byte usually 0/1, exact meaning unconfirmed)
        +0x10  16 B   reserved (always zero in observed files)
        +0x20  N*8 B  bindings, each = (u32 name_hash, u32 tag)
    """
    entry_hash: int
    flags1: int                 # high 24 bits of the count word
    flags2: int                 # full second u32 (after count)
    bindings: List[PropBinding] = field(default_factory=list)


@dataclass
class Pass:
    """An effect-pass: a named draw/dispatch within a technique.

    Pass names in observed files are short tokens such as 'p0'..'pN' or
    'P0', but can also be descriptive strings ('resolve_depth_min'). They
    appear in the trailing string pool of unknown_blob after the technique
    names.

    The stage->shader binding for a pass is implicit in the runtime: the
    effect lists its global VS/PS/GS/DS/HS/CS shader indices, and the
    engine picks one of each per pass at draw time using state set via
    SetTechnique/SetPass. The .awc file does not store an explicit
    pass-index -> shader-index table; the binding is recovered by ordinal
    correspondence (pass N uses the Nth shader of each stage that the
    effect uses for that technique).
    """
    name: str


@dataclass
class Technique:
    """An effect-technique: a named group of passes.

    Techniques are the top-level switchable variants of an effect. Each
    technique has one or more passes. The string pool stores technique
    names contiguously followed by pass names; the structural split
    between them is recovered by inspecting the trailing tokens (pass
    names start with 'p' or 'P' followed by digits, OR by detecting the
    'pass section' on the boundary).
    """
    name: str
    passes: List[Pass] = field(default_factory=list)


# ---------------------------------------------------------------------------
# pre_proplst region (per-effect shader-reflection blob).
# ---------------------------------------------------------------------------
# Decoded structure (verified byte-identical round-trip across all 397
# effects in the two reference files):
#
#   +0x00  8 B   zero-prefix (always zero in observed files)
#   +0x08  N B   header_block
#                  Per-shader-program metadata table; opaque (no full decode
#                  yet). Size N is variable per effect — bounded structurally
#                  by where the param_section begins (first valid 28-byte
#                  register record). N has been observed in the range
#                  ~440..7400 bytes.
#   +0x08+N  P B param_section
#                  Back-to-back records, two kinds (auto-detected by their
#                  internal invariants):
#                    * 28-byte param record (matches Shader.Register layout):
#                        u16 resource_type (D3D12, see ResourceType enum)
#                        u16 register_slot
#                        u8  cb_count, ndesc, space, reserved
#                        u16 cb_data_offset (relative to record start)
#                        u16 name_offset    (relative to record start)
#                        16 B extra: padding + cb_size + padding + 8 B name
#                                    hash pair. For non-samplers the pair is
#                                    (h, h); for samplers it is
#                                    (joaat('_'+name), joaat(name)).
#                    * 24-byte CBufferData record (cbuffer member entry):
#                        u16 type (ValueType, 0..14)
#                        u16 array_size
#                        u16 pack_offset
#                        u32 name_offset    (relative to record start)
#                        14 B extra: padding + hash dup (h, h) + padding
#   +0x08+N+P  D B  defaults_and_strings
#                  The block between the last param/cbuffer record and the
#                  40-byte invariant tail. Contains:
#                    * String pool (NUL-prefixed, NUL-terminated tokens —
#                      parameter / cbuffer / sampler / global names).
#                    * Defaults blob (32-bit floats interleaved with packing
#                      info — for cbuffer-member default values referenced
#                      by pack_offset). Defaults may appear before AND/OR
#                      after the strings — boundary is not deterministically
#                      decodable without a full layout description, so this
#                      sub-region is preserved verbatim as one byte slice.
#   end-T-40   40 B invariant_tail
#                  One of THREE observed byte-exact variants — selected by
#                  the proplst record's register-slot field (variant index
#                  matches the slot value). Each variant is:
#                    12 B sub-header (count+1+offset triple)
#                    28 B proplst param record (type=0x0430 ConstantBuffer,
#                                               hash dup = 0x2b5935e4)
#   end-T      T B  trailer
#                  1, 9, or 17 bytes:
#                    * Optional pairs of (u32 name_hash, u32 count) — 0, 1,
#                      or 2 pairs in observed data.
#                    * Always a final 0x00 byte.
#                  Trailer pairs reference cbuffer/global names; their
#                  exact runtime purpose is unknown but the values are
#                  preserved verbatim.
_PRE_PROPLST_PROPLST_HASH = 0xe435592b  # joaat_lower('proplst')
_PRE_PROPLST_PROPLST_DUP = b'\x2b\x59\x35\xe4\x2b\x59\x35\xe4'
_PRE_PROPLST_INVARIANT_VARIANTS = (
    # variant 0 (slot=0): 200/397 effects
    bytes.fromhex(
        '2d0000000100000000002d00'
        '300400000000010000001d001d000000000000002b5935e42b5935e4'),
    # variant 1 (slot=0x100): 176/397 effects
    bytes.fromhex(
        '350000000100000001003500'
        '300400010000010000002500250000001c0000002b5935e42b5935e4'),
    # variant 2 (slot=0x200): 21/397 effects
    bytes.fromhex(
        '3d0000000100000002003d00'
        '300400020000010000002d002d0000001c0000002b5935e42b5935e4'),
)


@dataclass
class PreProplstRegion:
    """Structured representation of the pre_proplst region.

    Sub-regions are preserved as bytes slices for byte-identical
    round-trip. See the module-level comment block above for the
    full byte-level layout.
    """
    # Variable header block immediately after the 8-byte zero prefix.
    # Contains per-shader-program metadata; treated as opaque bytes.
    header_block: bytes = b''
    # Contiguous run of 28-byte param records and 24-byte CBufferData
    # records. Auto-detected via their internal invariants (type field +
    # name-hash references into the string pool).
    param_section: bytes = b''
    # Mixed strings pool + defaults blob, between the last param/cbuffer
    # record and the 40-byte invariant tail. Preserved verbatim.
    defaults_and_strings: bytes = b''
    # Which of the 3 observed invariant_tail variants is used.
    invariant_variant: int = 0
    # Trailer pairs of (name_hash, count) — 0, 1, or 2 pairs followed by
    # a single NUL byte. The runtime semantics of these pairs is unknown
    # but the values are byte-identical-preserved.
    trailer_pairs: List[tuple] = field(default_factory=list)

    def encode(self) -> bytes:
        """Encode back to on-disk bytes."""
        import struct as _s
        out = bytearray(b'\x00' * 8)
        out += self.header_block
        out += self.param_section
        out += self.defaults_and_strings
        out += _PRE_PROPLST_INVARIANT_VARIANTS[self.invariant_variant]
        for h, c in self.trailer_pairs:
            out += _s.pack('<II', h & 0xFFFFFFFF, c & 0xFFFFFFFF)
        out += b'\x00'
        return bytes(out)


@dataclass
class Effect:
    """An effect record from the trailer (FxDb-equivalent entry).

    Each effect groups a set of shader programs (VS/PS/GS/DS/HS/CS), tied
    together with techniques and passes. The complete on-disk layout of
    one Effect record is:

        u8     name_len_plus_one              (= len(name)+1, includes NUL)
        bytes  name[name_len_plus_one - 1]
        u8     0x00                            (NUL terminator)
        u32    data_buffer_size               (see field doc below; was `unknown_u32`)
        u32    vs_count; u32[vs_count]        global VS indices
        u32    ps_count; u32[ps_count]        global PS indices
        u32    gs_count; u32[gs_count]
        u32    ds_count; u32[ds_count]
        u32    hs_count; u32[hs_count]
        u32    cs_count; u32[cs_count]
        bytes  pre_proplst_region             see PreProplstRegion above
                                              (decoded into pre_proplst_region;
                                               the legacy `pre_proplst` property
                                               re-encodes the bytes for back-
                                               compat).
        bytes  literal `proplst\\0`            (8 bytes — section marker; not
                                              stored — recreated on encode)
        u32    prop_entry_count
        for i in 0..prop_entry_count-1:
            u32    entry_size
            bytes  entry[entry_size]          PropEntry layout (see PropEntry)
        u32    strings_size
        bytes  strings_data[strings_size]     leading NUL + null-terminated
                                              tokens: sampler/global names,
                                              technique names, pass names
    """
    name: str
    # First u32 of the payload, immediately after the length-prefixed name.
    # Identified as `DataBufferSize` from Neodymium's reverse-engineering of
    # the SGD1/SGDB sibling format (RDR3 fxdb) — see CodeX `FxdbEffect.Read()`.
    # In SGD1 it bounds the per-effect data region; in SGD2 we preserve it
    # verbatim. Treated as opaque since we don't yet drive any length math off
    # it (round-trip works via raw byte preservation).
    data_buffer_size: int = 0
    # Per-stage indices into the global SGD2 shader arrays. Each list is the
    # subset of shaders (by global index) that this effect uses for that stage.
    # Stored in file order: VS, PS, GS, DS, HS, CS.
    vs_indices: List[int] = field(default_factory=list)
    ps_indices: List[int] = field(default_factory=list)
    gs_indices: List[int] = field(default_factory=list)
    ds_indices: List[int] = field(default_factory=list)
    hs_indices: List[int] = field(default_factory=list)
    cs_indices: List[int] = field(default_factory=list)
    # The pre-proplst region — fully decoded into a structured representation
    # (see PreProplstRegion above). Round-trips byte-identical to the original
    # on-disk bytes via `pre_proplst_region.encode()`.
    pre_proplst_region: PreProplstRegion = field(default_factory=PreProplstRegion)
    # Decoded 'proplst' table — see PropEntry.
    prop_entries: List[PropEntry] = field(default_factory=list)
    # The trailing string pool of this effect, as parsed null-terminated
    # tokens (the leading NUL byte and any trailing NUL are stripped).
    # Partitioned best-effort into samplers/globals, techniques, and passes
    # below.
    strings: List[str] = field(default_factory=list)
    # Decoded technique/pass tree. The split between "globals/samplers" and
    # "techniques" and "passes" is heuristic: tokens starting with '_' are
    # samplers; tokens matching pN/PN are passes; the rest are techniques.
    # Each technique has its passes split off the trailing pN block; passes
    # are assigned in order across techniques where ambiguous.
    techniques: List[Technique] = field(default_factory=list)
    # Names that appear in the strings pool but are samplers/globals (not
    # techniques or passes). Exposed separately so the GUI can distinguish.
    sampler_names: List[str] = field(default_factory=list)

    # --- Backwards-compat shims (preserve old field names used elsewhere) ---
    @property
    def unknown_u32(self) -> int:
        """Legacy alias for `data_buffer_size` (renamed to match SGD1's name)."""
        return self.data_buffer_size

    @unknown_u32.setter
    def unknown_u32(self, value: int) -> None:
        self.data_buffer_size = value

    @property
    def technique_names(self) -> List[str]:
        """Legacy: flat list of tokens (samplers + techniques + passes).

        Pre-decode callers used this as a string-scan dump of the blob's
        trailing portion. Returned in original on-disk order.
        """
        return list(self.strings)

    @property
    def pre_proplst(self) -> bytes:
        """Legacy alias for `pre_proplst_region.encode()`.

        Kept for backwards-compatibility with code that read the
        previously-opaque bytes blob. The region is now structurally
        decoded; this property re-emits its on-disk bytes.
        """
        return self.pre_proplst_region.encode()

    @property
    def unknown_blob(self) -> bytes:
        """Legacy: round-trip the full blob =
            pre_proplst + b'proplst\\0' + prop body + strings record.
        """
        import struct as _s
        out = bytearray(self.pre_proplst_region.encode())
        out += b'proplst\x00'
        out += _s.pack('<I', len(self.prop_entries))
        for pe in self.prop_entries:
            body = _encode_prop_entry(pe)
            out += _s.pack('<I', len(body)) + body
        strdata = b'\x00' + b''.join(
            (s.encode('latin-1') + b'\x00') for s in self.strings
        )
        # Strings record's leading NUL is already added; if strings is
        # empty, the section still has a single NUL placeholder to match
        # observed format (TODO confirm — currently no all-empty effects
        # in observed files).
        out += _s.pack('<I', len(strdata)) + strdata
        return bytes(out)


# Sentinel kept for backwards-import compatibility (was imported by older
# parser revisions; no longer used).
_PROPLST_INVARIANT_PREAMBLE = b''


def _encode_prop_entry(pe: 'PropEntry') -> bytes:
    """Encode a PropEntry back to its on-disk byte representation."""
    import struct as _s
    out = bytearray()
    out += _s.pack('<Q', pe.entry_hash)
    count_word = (len(pe.bindings) & 0xFF) | ((pe.flags1 & 0xFFFFFF) << 8)
    out += _s.pack('<I', count_word)
    out += _s.pack('<I', pe.flags2)
    out += b'\x00' * 16
    for b in pe.bindings:
        out += _s.pack('<II', b.name_hash & 0xFFFFFFFF, b.tag & 0xFFFFFFFF)
    return bytes(out)


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
    # Trailer (effects database). May be empty if the file has no trailer.
    effects: List[Effect] = field(default_factory=list)
    # Raw trailer prefix preserved verbatim: 8 leading zero bytes + the u32
    # effect-count. Kept so the trailer can be round-tripped if a writer
    # is ever added. TODO: confirm the 8 leading bytes are always zero.
    trailer_header: bytes = field(default=b'')
    # Any leftover trailing bytes that weren't consumed by effect parsing.
    # Should normally be empty; non-empty indicates an incomplete decode.
    trailer_unparsed_tail: bytes = field(default=b'')

    @property
    def total_shader_count(self) -> int:
        return (len(self.vertex_shaders) + len(self.pixel_shaders) +
                len(self.geometry_shaders) + len(self.domain_shaders) +
                len(self.hull_shaders) + len(self.compute_shaders))
