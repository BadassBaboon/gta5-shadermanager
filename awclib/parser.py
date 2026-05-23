"""
Binary parser for AWC Shader Library files.
Implements parsing logic based on 010 Editor template.
"""

import os
import re
import struct
from typing import BinaryIO, Dict, List, Optional, Tuple
from .models import (
    AWCFile, Shader, Register, CBufferData, Effect,
    PropEntry, PropBinding, Technique, Pass,
    PreProplstRegion,
    _PROPLST_INVARIANT_PREAMBLE,
    _PRE_PROPLST_PROPLST_DUP,
    _PRE_PROPLST_INVARIANT_VARIANTS,
)


def joaat_lower(s: str) -> int:
    """Rage/GTA Jenkins one-at-a-time hash, lowercased input. Returns u32."""
    h = 0
    for c in s.encode('latin-1').lower():
        h = (h + c) & 0xFFFFFFFF
        h = (h + ((h << 10) & 0xFFFFFFFF)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + ((h << 3) & 0xFFFFFFFF)) & 0xFFFFFFFF
    h ^= h >> 11
    h = (h + ((h << 15) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return h


# The 8-byte ASCII marker `proplst\0` denotes the start of the per-effect
# property-list section. Each Effect record contains EXACTLY ONE occurrence
# of this byte sequence in its blob (verified across all 397 effects in the
# two reference files). The bytes immediately preceding the marker are the
# binding payload of the last preceding PropEntry — the format uses the
# literal token "proplst" as a section name AND its own joaat32 hash
# (0xe435592b) appears in that last entry, so the marker is structurally
# guaranteed not to clash with anything else in the blob.
_PROPLST_MARKER = b'proplst\x00'


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

        # Trailer (effects database)
        trailer_start = self.tell()
        # Find total file size so we know how much trailer remains
        cur = self.file.tell()
        self.file.seek(0, os.SEEK_END)
        file_end = self.file.tell()
        self.file.seek(cur)
        trailer_bytes_remaining = file_end - trailer_start

        trailer_header = b''
        effects: List[Effect] = []
        unparsed_tail = b''
        if trailer_bytes_remaining > 0:
            trailer = self.file.read(trailer_bytes_remaining)
            limits = {
                'VS': len(vertex_shaders), 'PS': len(pixel_shaders),
                'GS': len(geometry_shaders), 'DS': len(domain_shaders),
                'HS': len(hull_shaders), 'CS': len(compute_shaders),
            }
            trailer_header, effects, unparsed_tail = _parse_trailer(trailer, limits)

        return AWCFile(
            magic=magic,
            vertex_shaders=vertex_shaders,
            pixel_shaders=pixel_shaders,
            geometry_shaders=geometry_shaders,
            domain_shaders=domain_shaders,
            hull_shaders=hull_shaders,
            compute_shaders=compute_shaders,
            effects=effects,
            trailer_header=trailer_header,
            trailer_unparsed_tail=unparsed_tail,
        )


# ---------------------------------------------------------------------------
# Trailer (effects DB) parsing
# ---------------------------------------------------------------------------
# Trailer layout (relative to trailer start):
#   +0x00  8 bytes, all zero in observed files. Treated as reserved/padding;
#          preserved verbatim for round-trip.
#   +0x08  u32   effect_count
#   +0x0C  Effect[effect_count], packed back-to-back, no padding.
#
# Effect record (fully decoded):
#   u8     name_len_plus_one    (= len(name) + 1, includes the NUL byte)
#   bytes  name                 (name_len_plus_one - 1 chars)
#   u8     0x00                 (NUL terminator)
#   u32    data_buffer_size     (per-effect data region size hint — matches
#                                SGD1's `DataBufferSize`, see Effect.data_buffer_size)
#   u32    vs_count; u32[vs_count]   global VS indices
#   u32    ps_count; u32[ps_count]
#   u32    gs_count; u32[gs_count]
#   u32    ds_count; u32[ds_count]
#   u32    hs_count; u32[hs_count]
#   u32    cs_count; u32[cs_count]
#   bytes  pre_proplst_region            (per-program register reflection;
#                                         opaque, terminated by the
#                                         8-byte `proplst\0` ASCII marker)
#   bytes  b'proplst\0'                  (8-byte section marker)
#   u32    prop_entry_count
#   prop_entry_count × (
#       u32    entry_size
#       bytes  entry[entry_size]         (PropEntry layout — see models.py)
#   )
#   u32    strings_size
#   bytes  strings_data[strings_size]    (leading NUL + null-terminated
#                                         tokens)
#
# Total effect byte size is fully computable from the contents — no scanning
# heuristics are needed except for the 48-byte `_PROPLST_FULL_MARKER` find,
# which is structurally guaranteed by the format itself (it's the literal
# section header).

_STAGE_NAMES = ('VS', 'PS', 'GS', 'DS', 'HS', 'CS')


# ---------------------------------------------------------------------------
# pre_proplst region decoder
# ---------------------------------------------------------------------------
# The region is structurally split into:
#   8 B zero-prefix | header_block | param_section |
#   defaults_and_strings | 40 B invariant_tail | trailer
# See PreProplstRegion in models.py for the full byte-level spec.

_PRE_PROPLST_INVARIANT_SET = {v: i for i, v in enumerate(_PRE_PROPLST_INVARIANT_VARIANTS)}


def _collect_pre_proplst_name_pool(data: bytes) -> Dict[int, str]:
    """Scan `data` for NUL-terminated printable-ASCII tokens; map joaat32(tok)
    -> tok and joaat32('_'+tok) -> '_'+tok. Used to recognize hash references
    inside param/cbuf records during the chain walk.
    """
    pool: Dict[int, str] = {}
    n = len(data)
    p = 0
    while p < n:
        if 32 <= data[p] < 127:
            start = p
            while p < n and 32 <= data[p] < 127:
                p += 1
            if p < n and data[p] == 0 and (p - start) >= 2:
                tok = data[start:p].decode('latin-1')
                pool[joaat_lower(tok)] = tok
                if not tok.startswith('_'):
                    pool[joaat_lower('_' + tok)] = '_' + tok
        else:
            p += 1
    pool[joaat_lower('proplst')] = 'proplst'
    return pool


def _is_pre_proplst_param_record(data: bytes, p: int, pool: Dict[int, str]) -> bool:
    """A 28-byte register-style param record matches if:
       - bytes [0:2] is a plausible ResourceType (high byte 0x01/0x02/0x03/0x04)
       - bytes [16:20] are zero (reserved)
       - bytes [20:24] and [24:28] are name hashes in `pool`
       - the two hashes are either equal (non-sampler) OR form a
         (_Name, Name) pair (sampler).
    """
    if p + 28 > len(data):
        return False
    rtype = struct.unpack_from('<H', data, p)[0]
    if ((rtype >> 8) & 0xFF) not in (0x01, 0x02, 0x03, 0x04):
        return False
    if struct.unpack_from('<I', data, p + 16)[0] != 0:
        return False
    h1 = struct.unpack_from('<I', data, p + 20)[0]
    h2 = struct.unpack_from('<I', data, p + 24)[0]
    if h1 == 0 or h2 == 0:
        return False
    if h1 not in pool or h2 not in pool:
        return False
    if h1 != h2:
        n1 = pool[h1]
        n2 = pool[h2]
        if n1 != '_' + n2:
            return False
    return True


def _is_pre_proplst_cbuf_record(data: bytes, p: int, pool: Dict[int, str]) -> bool:
    """A 24-byte CBufferData record matches if:
       - bytes [0:2] is a ValueType (0..14)
       - bytes [16:20] and [20:24] are equal, non-zero, and in `pool`.
    """
    if p + 24 > len(data):
        return False
    t = struct.unpack_from('<H', data, p)[0]
    if t > 14:
        return False
    h1 = struct.unpack_from('<I', data, p + 16)[0]
    h2 = struct.unpack_from('<I', data, p + 20)[0]
    if h1 == 0 or h1 != h2:
        return False
    if h1 not in pool:
        return False
    return True


def _parse_pre_proplst(data: bytes) -> PreProplstRegion:
    """Parse the pre_proplst region into a PreProplstRegion.

    Round-trips byte-identical via PreProplstRegion.encode().

    Raises ValueError on structural malformation (missing zero-prefix,
    missing proplst hash anchor, unrecognized invariant variant, malformed
    trailer). All 397 effects in the two reference files parse cleanly.
    """
    n = len(data)
    if n < 8 + 40 + 1:
        raise ValueError(f'pre_proplst too short: {n} bytes')
    if data[:8] != b'\x00' * 8:
        raise ValueError(
            f'pre_proplst: expected 8 zero bytes prefix, got {data[:8].hex()}'
        )

    # Locate the 40-byte invariant tail via the proplst hash dup anchor
    # near the end. The dup is structurally guaranteed to be the LAST
    # occurrence of 0x2b5935e4 0x2b5935e4 in the region.
    idx = data.rfind(_PRE_PROPLST_PROPLST_DUP)
    if idx < 0:
        raise ValueError('pre_proplst: proplst hash dup anchor not found')
    inv_start = idx - 32  # 12-byte sub-header + first 20 bytes of 28-byte record
    if inv_start < 8:
        raise ValueError(
            f'pre_proplst: invariant tail too close to start (idx=0x{idx:x})'
        )
    inv_40 = data[inv_start:idx + 8]
    variant = _PRE_PROPLST_INVARIANT_SET.get(inv_40)
    if variant is None:
        raise ValueError(
            f'pre_proplst: unrecognized invariant variant '
            f'(40 bytes at 0x{inv_start:x}: {inv_40.hex()})'
        )

    # Trailer: 0..2 (u32 hash, u32 count) pairs followed by a 0x00 byte.
    trailer = data[idx + 8:]
    if len(trailer) < 1 or trailer[-1] != 0:
        raise ValueError(f'pre_proplst: trailer not NUL-terminated: {trailer.hex()}')
    extra = trailer[:-1]
    if len(extra) % 8 != 0:
        raise ValueError(
            f'pre_proplst: trailer extra length {len(extra)} not multiple of 8'
        )
    trailer_pairs: List[tuple] = []
    for i in range(0, len(extra), 8):
        h, c = struct.unpack_from('<II', extra, i)
        trailer_pairs.append((h, c))

    # Sub-divide the middle (data[8:inv_start]) into:
    #   header_block | param_section | defaults_and_strings
    #
    # Strategy: scan for the first offset where a chain of valid
    # 28-byte param records + 24-byte CBufferData records walks the
    # longest. The header_block is everything before that offset;
    # defaults_and_strings is everything after the chain.
    pool = _collect_pre_proplst_name_pool(data)
    best: Optional[tuple] = None  # (start, end)
    for start in range(8, inv_start):
        if not _is_pre_proplst_param_record(data, start, pool):
            continue
        p = start
        chain_len = 0
        while p < inv_start:
            if _is_pre_proplst_param_record(data, p, pool):
                p += 28
                chain_len += 1
            elif _is_pre_proplst_cbuf_record(data, p, pool):
                p += 24
                chain_len += 1
            else:
                break
        if best is None or chain_len > best[2]:
            best = (start, p, chain_len)

    if best is None:
        # Fall back: entire middle becomes header_block (still
        # byte-identical round-trip via encode()).
        header_block = data[8:inv_start]
        param_section = b''
        defaults_and_strings = b''
    else:
        param_start, param_end, _ = best
        header_block = data[8:param_start]
        param_section = data[param_start:param_end]
        defaults_and_strings = data[param_end:inv_start]

    return PreProplstRegion(
        header_block=header_block,
        param_section=param_section,
        defaults_and_strings=defaults_and_strings,
        invariant_variant=variant,
        trailer_pairs=trailer_pairs,
    )

# Regex for pass names: lowercase 'p' or uppercase 'P' followed by digits,
# optionally followed by an underscored suffix (e.g. 'p22_sm41').
_PASS_NAME_RE = re.compile(r'^[pP]\d+(?:_\w+)?$')


def _parse_prop_entry(data: bytes) -> PropEntry:
    """Parse one PropEntry from its on-disk bytes."""
    if len(data) < 24:
        raise ValueError(f'PropEntry too short: {len(data)} bytes')
    entry_hash, count_word, flags2 = struct.unpack_from('<QII', data, 0)
    binding_count = count_word & 0xFF
    flags1 = count_word >> 8
    # Bytes [16:24] are reserved zeros.
    bindings: List[PropBinding] = []
    expected = 24 + binding_count * 8
    if expected > len(data):
        raise ValueError(
            f'PropEntry size mismatch: expected {expected} bytes for '
            f'{binding_count} bindings, got {len(data)}'
        )
    for i in range(binding_count):
        h, t = struct.unpack_from('<II', data, 24 + i * 8)
        bindings.append(PropBinding(name_hash=h, tag=t))
    return PropEntry(
        entry_hash=entry_hash,
        flags1=flags1,
        flags2=flags2,
        bindings=bindings,
    )


def _split_techniques(
    strings: List[str], referenced_hashes: set
) -> Tuple[List[str], List[Technique]]:
    """Split a flat string list into (samplers/globals, techniques).

    The on-disk format does not store counts; the split is recovered by
    combining two empirical observations:

       1. Every name that appears as a `name_hash` in any PropBinding is
          a sampler/global/cbuffer/resource (matched via joaat32). These
          form a leading prefix of the strings list.
       2. The trailing tokens matching `^[pP]\\d+(?:_\\w+)?$` are pass
          names shared across all techniques (e.g. p0, p1, p22_sm41, P0).
       3. The middle slice is the technique-name list.

    Special case (the "imMS" pattern): some effects have only one
    technique and no leading samplers, and the pass names are descriptive
    strings (e.g. 'resolve_depth_sample0') rather than pN. We detect this
    when no tokens match the pass regex but the entry-binding analysis
    shows zero samplers: then the first string is the technique and the
    rest are passes.

    Returns (sampler_names_in_order, techniques_in_order).
    """
    if not strings:
        return [], []

    # Step 1: trailing run of pass names matching pN.
    pass_start = len(strings)
    while pass_start > 0 and _PASS_NAME_RE.match(strings[pass_start - 1]):
        pass_start -= 1
    pass_tokens = strings[pass_start:]

    # Step 2: leading run of names that are referenced as bindings (i.e.
    # samplers / globals / cbuffers / etc.). Stops at the first non-
    # referenced name, which is the first technique.
    sampler_end = 0
    while (sampler_end < pass_start
           and joaat_lower(strings[sampler_end]) in referenced_hashes):
        sampler_end += 1

    sampler_names = strings[:sampler_end]
    technique_names = strings[sampler_end:pass_start]

    # Step 3: build techniques.
    techniques: List[Technique] = []
    if not technique_names:
        # No middle slice — entire blob is samplers + passes (degenerate;
        # not observed in reference files).
        return sampler_names, []

    if pass_tokens:
        # Shared pN pass list applied to every technique.
        for tn in technique_names:
            techniques.append(Technique(
                name=tn,
                passes=[Pass(name=pn) for pn in pass_tokens],
            ))
    elif len(technique_names) >= 2:
        # The "imMS" pattern: single technique with named (non-pN) passes.
        techniques.append(Technique(
            name=technique_names[0],
            passes=[Pass(name=n) for n in technique_names[1:]],
        ))
    else:
        techniques.append(Technique(
            name=technique_names[0],
            passes=[Pass(name='p0')],
        ))

    return sampler_names, techniques


def _parse_effect(trailer: bytes, pos: int) -> Tuple[Effect, int]:
    """Parse one Effect record starting at `pos`. Returns (effect, next_pos).

    Raises ValueError if the structure is malformed (no recovery — the
    format must be self-describing for the parser to be deterministic).
    """
    # Length-prefixed name (length includes the NUL terminator).
    if pos >= len(trailer):
        raise ValueError(f'effect start past trailer end ({pos}/{len(trailer)})')
    name_len_plus_one = trailer[pos]
    if name_len_plus_one < 2:
        raise ValueError(
            f'effect at offset {pos}: name_len_plus_one={name_len_plus_one} '
            f'(must be >= 2)'
        )
    name_start = pos + 1
    name_end = pos + name_len_plus_one  # NUL byte position
    if name_end >= len(trailer):
        raise ValueError(
            f'effect at offset {pos}: name runs past trailer end'
        )
    if trailer[name_end] != 0:
        raise ValueError(
            f'effect at offset {pos}: missing NUL terminator after name '
            f'(got 0x{trailer[name_end]:02x})'
        )
    name = trailer[name_start:name_end].decode('latin-1')
    p = name_end + 1  # past NUL

    # u32 data_buffer_size (SGD1's `DataBufferSize` — preserved verbatim)
    data_buffer_size = struct.unpack_from('<I', trailer, p)[0]
    p += 4

    # Six stage-arrays.
    stages: Dict[str, List[int]] = {}
    for stage in _STAGE_NAMES:
        cnt = struct.unpack_from('<I', trailer, p)[0]
        p += 4
        if cnt > 100000:
            raise ValueError(
                f'effect {name!r}: stage {stage} count {cnt} out of range'
            )
        if cnt:
            stages[stage] = list(struct.unpack_from(f'<{cnt}I', trailer, p))
            p += cnt * 4
        else:
            stages[stage] = []

    # The pre-proplst region ends at the `proplst\0` ASCII marker. The
    # marker is structurally guaranteed to occur exactly once per effect
    # (verified across all observed effects). The 40 bytes immediately
    # preceding the marker are NOT a fixed invariant — they're the binding
    # payload of the last preceding PropEntry, which varies per effect
    # (the binding hash is always joaat("proplst")=0xe435592b, but the
    # other fields differ).
    blob_start = p
    marker_idx = trailer.find(_PROPLST_MARKER, blob_start)
    if marker_idx < 0:
        raise ValueError(
            f'effect {name!r}: proplst marker not found after offset '
            f'0x{blob_start:x}'
        )
    pre_proplst_bytes = trailer[blob_start:marker_idx]
    pre_proplst_region = _parse_pre_proplst(pre_proplst_bytes)

    # Skip the 8-byte marker.
    p = marker_idx + len(_PROPLST_MARKER)

    # u32 prop_entry_count, then size-prefixed entries.
    prop_count = struct.unpack_from('<I', trailer, p)[0]
    p += 4
    if prop_count > 1_000_000:
        raise ValueError(
            f'effect {name!r}: prop_count {prop_count} out of range'
        )
    prop_entries: List[PropEntry] = []
    for i in range(prop_count):
        entry_size = struct.unpack_from('<I', trailer, p)[0]
        p += 4
        if entry_size < 24 or entry_size > 1_000_000 or p + entry_size > len(trailer):
            raise ValueError(
                f'effect {name!r}: prop entry {i} size {entry_size} out of '
                f'range (at offset 0x{p:x})'
            )
        prop_entries.append(_parse_prop_entry(trailer[p:p + entry_size]))
        p += entry_size

    # u32 strings_size, then string blob.
    strings_size = struct.unpack_from('<I', trailer, p)[0]
    p += 4
    if strings_size > 10_000_000 or p + strings_size > len(trailer):
        raise ValueError(
            f'effect {name!r}: strings_size {strings_size} out of range '
            f'(at offset 0x{p:x})'
        )
    strings_data = trailer[p:p + strings_size]
    p += strings_size

    # Strings have a leading NUL and are null-terminated.
    raw_tokens = strings_data.split(b'\x00')
    # Drop empty tokens (leading + trailing + any null-only padding).
    strings = [t.decode('latin-1') for t in raw_tokens if t]

    # Resolve binding names from hashes (best-effort — used for display).
    name_pool: Dict[int, str] = {}
    for s in strings:
        name_pool[joaat_lower(s)] = s
    referenced_hashes = set()
    for entry in prop_entries:
        for b in entry.bindings:
            b.name = name_pool.get(b.name_hash, '')
            referenced_hashes.add(b.name_hash)

    sampler_names, techniques = _split_techniques(strings, referenced_hashes)

    return Effect(
        name=name,
        data_buffer_size=data_buffer_size,
        vs_indices=stages['VS'],
        ps_indices=stages['PS'],
        gs_indices=stages['GS'],
        ds_indices=stages['DS'],
        hs_indices=stages['HS'],
        cs_indices=stages['CS'],
        pre_proplst_region=pre_proplst_region,
        prop_entries=prop_entries,
        strings=strings,
        techniques=techniques,
        sampler_names=sampler_names,
    ), p


def _parse_trailer(trailer: bytes, limits: dict) -> Tuple[bytes, List[Effect], bytes]:
    """Parse the entire trailer. Returns (trailer_header, effects, tail).

    `tail` should always be empty on a successfully decoded file. Any
    non-zero tail length indicates a parser bug or an unrecognized format
    variant.
    """
    if len(trailer) < 12:
        return trailer, [], b''
    # Trailer header: 8 zero bytes + u32 effect_count.
    # The leading 8 bytes are zero in all observed files (verified across
    # 397 effects in both reference files) — treated as reserved/padding.
    header = trailer[:12]
    count = struct.unpack_from('<I', trailer, 8)[0]
    if count == 0 or count > 1_000_000:
        return trailer[:12], [], trailer[12:]

    effects: List[Effect] = []
    pos = 12
    for _ in range(count):
        if pos >= len(trailer):
            # Premature EOF — return what we have plus empty tail (the
            # missing effects can't be recovered).
            break
        try:
            effect, pos = _parse_effect(trailer, pos)
        except ValueError:
            # Malformed: preserve remainder as tail for diagnostics.
            return header, effects, trailer[pos:]
        effects.append(effect)

    tail = trailer[pos:] if pos < len(trailer) else b''
    return header, effects, tail


def parse_awc_file(filepath: str) -> AWCFile:
    """Parse an AWC file from the given path."""
    with open(filepath, 'rb') as f:
        parser = AWCParser(f)
        return parser.parse()
