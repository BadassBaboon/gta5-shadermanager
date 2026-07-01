import struct
import io

# --- Helpers ---

class BinaryReader:
    def __init__(self, data):
        self.stream = io.BytesIO(data)

    def read_byte(self):
        return struct.unpack('B', self.stream.read(1))[0]

    def read_uint16(self):
        return struct.unpack('<H', self.stream.read(2))[0]

    def read_uint32(self):
        return struct.unpack('<I', self.stream.read(4))[0]
    
    def read_int32(self):
        return struct.unpack('<i', self.stream.read(4))[0]

    def read_float(self):
        return struct.unpack('<f', self.stream.read(4))[0]

    def read_bytes(self, length):
        return self.stream.read(length)

    def read_string(self):
        length = self.read_byte()
        if length == 0:
            return ""
        # C# пишет длину (N), а затем N-1 символов + 1 нуль-терминатор (обычно), 
        # но код CodeWalker читает (len) байт и берет строку utf-8.
        # В C# коде: (sl > 1) ? Encoding.ASCII.GetString(ba, 0, sl - 1) : string.Empty;
        data = self.stream.read(length)
        if length > 1:
            return data[:-1].decode('ascii', errors='ignore')
        return ""

    def read_string_array(self):
        count = self.read_byte()
        if count == 0:
            return []
        res = []
        for _ in range(count):
            res.append(self.read_string())
        return res

    def pos(self):
        return self.stream.tell()

    def seek(self, pos):
        self.stream.seek(pos)


class BinaryWriter:
    def __init__(self):
        self.stream = io.BytesIO()

    def write_byte(self, val):
        self.stream.write(struct.pack('B', val))

    def write_uint16(self, val):
        self.stream.write(struct.pack('<H', val))

    def write_uint32(self, val):
        self.stream.write(struct.pack('<I', val))
    
    def write_int32(self, val):
        self.stream.write(struct.pack('<i', val))

    def write_float(self, val):
        self.stream.write(struct.pack('<f', val))

    def write_bytes(self, val):
        self.stream.write(val)

    def write_string(self, s):
        if not s:
            self.write_byte(0)
        else:
            if len(s) > 255: s = s[:255]
            encoded = s.encode('ascii')
            # Длина = длина байт + 1 (для null-терминатора, который C# пишет неявно в массив)
            # В C# WriteString: write(len+1), write(bytes), write(0)
            self.write_byte(len(encoded) + 1)
            self.stream.write(encoded)
            self.write_byte(0)

    def write_string_array(self, arr):
        count = len(arr) if arr else 0
        self.write_byte(count)
        if arr:
            for s in arr:
                self.write_string(s)

    def get_data(self):
        return self.stream.getvalue()

# --- Classes mirroring C# structures ---

class FxcPresetParam:
    def read(self, br):
        self.Name = br.read_string()
        self.Unused0 = br.read_byte()
        self.Value = br.read_uint32()

    def write(self, bw):
        bw.write_string(self.Name)
        bw.write_byte(self.Unused0)
        bw.write_uint32(self.Value)

class FxcShaderBufferRef:
    def read(self, br):
        self.Name = br.read_string()
        self.Slot = br.read_uint16()
    
    def write(self, bw):
        bw.write_string(self.Name)
        bw.write_uint16(self.Slot)

class FxcShader:
    def __init__(self):
        self.Name = ""
        self.Variables = []
        self.Buffers = []
        self.ByteCode = b""
        self.VersionMajor = 0
        self.VersionMinor = 0
        self.OffsetBy1 = False
        self.Type = 0

    def read(self, br, gindex):
        self.Type = gindex
        self.Name = br.read_string()
        
        # Логика из C#: если имя пустое, читаем еще раз (хак для GS)
        if len(self.Name) == 0:
            self.Name = br.read_string()
            self.OffsetBy1 = True

        self.Variables = br.read_string_array()
        
        bufferCount = br.read_byte()
        self.Buffers = []
        for _ in range(bufferCount):
            buf = FxcShaderBufferRef()
            buf.read(br)
            self.Buffers.append(buf)

        if self.Type == 4: # GeometryShader
            exbyte = br.read_byte() # unused

        dataLength = br.read_uint32()
        if dataLength > 0:
            magic_dxbc = br.read_uint32() # Check for DXBC
            br.stream.seek(br.stream.tell() - 4) # Rewind
            
            self.ByteCode = br.read_bytes(dataLength)
            
            # Version parsing
            if self.Type in [0, 1, 4]: # VS, PS, GS
                self.VersionMajor = br.read_byte()
                self.VersionMinor = br.read_byte()

    def write(self, bw, gindex):
        if self.OffsetBy1:
            bw.write_byte(0)
        
        bw.write_string(self.Name)
        bw.write_string_array(self.Variables)
        
        count = len(self.Buffers)
        bw.write_byte(count)
        for buf in self.Buffers:
            buf.write(bw)

        if gindex == 4: # GS
            bw.write_byte(0)

        dataLength = len(self.ByteCode)
        bw.write_uint32(dataLength)
        if dataLength > 0:
            bw.write_bytes(self.ByteCode)
            if gindex in [0, 1, 4]:
                bw.write_byte(self.VersionMajor)
                bw.write_byte(self.VersionMinor)

class FxcShaderGroup:
    def __init__(self):
        self.Shaders = []
        self.Name = "NULL"
        self.Unk1Byte = 0
        self.Unk2Byte = 0
        self.Unk3Uint = 0
        self.OffsetBy1 = False

    def read(self, br, gindex):
        shaderCount = br.read_byte()
        if shaderCount == 0:
            # Hull shader skip logic
            shaderCount = br.read_byte()
            self.OffsetBy1 = True
        
        self.Name = br.read_string()
        self.Unk1Byte = br.read_byte()
        self.Unk2Byte = br.read_byte()
        self.Unk3Uint = br.read_uint32()

        if shaderCount > 1:
            for _ in range(1, shaderCount):
                sh = FxcShader()
                sh.read(br, gindex)
                self.Shaders.append(sh)

    def write(self, bw, gindex):
        shaderCount = len(self.Shaders) + 1
        
        if self.OffsetBy1:
            bw.write_byte(0)
        
        bw.write_byte(shaderCount)
        bw.write_string(self.Name)
        bw.write_byte(self.Unk1Byte)
        bw.write_byte(self.Unk2Byte)
        bw.write_uint32(self.Unk3Uint)

        for sh in self.Shaders:
            sh.write(bw, gindex)

class FxcVariableParam:
    def read(self, br):
        self.Name = br.read_string()
        self.Type = br.read_byte()
        if self.Type == 0: self.Value = br.read_int32()
        elif self.Type == 1: self.Value = br.read_float()
        elif self.Type == 2: self.Value = br.read_string()
    
    def write(self, bw):
        bw.write_string(self.Name)
        bw.write_byte(self.Type)
        if self.Type == 0: bw.write_int32(self.Value)
        elif self.Type == 1: bw.write_float(self.Value)
        elif self.Type == 2: bw.write_string(self.Value)

class FxcVariable:
    def read(self, br):
        self.Type = br.read_byte()
        self.Count = br.read_byte()
        self.Slot = br.read_byte()
        self.Group = br.read_byte()
        self.Name1 = br.read_string()
        self.Name2 = br.read_string()
        self.Offset = br.read_byte()
        self.Variant = br.read_byte()
        self.Unused0 = br.read_byte()
        self.Unused1 = br.read_byte()
        self.CBufferName = br.read_uint32()
        
        self.Params = []
        pCount = br.read_byte()
        for _ in range(pCount):
            p = FxcVariableParam()
            p.read(br)
            self.Params.append(p)
            
        self.Values = []
        vCount = br.read_byte()
        # Check if UINT or Float based on Type (simplified logic from C#)
        use_uint = self.Type in [11, 14, 7, 6, 15, 22, 21] 
        for _ in range(vCount):
            if use_uint: self.Values.append(br.read_uint32())
            else: self.Values.append(br.read_float())
        self.UseUInt = use_uint

    def write(self, bw):
        bw.write_byte(self.Type)
        bw.write_byte(self.Count)
        bw.write_byte(self.Slot)
        bw.write_byte(self.Group)
        bw.write_string(self.Name1)
        bw.write_string(self.Name2)
        bw.write_byte(self.Offset)
        bw.write_byte(self.Variant)
        bw.write_byte(self.Unused0)
        bw.write_byte(self.Unused1)
        bw.write_uint32(self.CBufferName)
        
        bw.write_byte(len(self.Params))
        for p in self.Params: p.write(bw)
        
        bw.write_byte(len(self.Values))
        for v in self.Values:
            if self.UseUInt: bw.write_uint32(v)
            else: bw.write_float(v)

class FxcCBuffer:
    def read(self, br):
        self.Size = br.read_uint32()
        self.Slots = [br.read_uint16() for _ in range(6)] # VS, PS, CS, DS, GS, HS
        self.Name = br.read_string()
    
    def write(self, bw):
        bw.write_uint32(self.Size)
        for s in self.Slots: bw.write_uint16(s)
        bw.write_string(self.Name)

class FxcPassParam:
    def read(self, br):
        self.Type = br.read_uint32()
        self.Value = br.read_uint32()
    def write(self, bw):
        bw.write_uint32(self.Type)
        bw.write_uint32(self.Value)

class FxcPass:
    def read(self, br):
        self.Indices = [br.read_byte() for _ in range(6)] # VS, PS, CS...
        pCount = br.read_byte()
        self.Params = []
        for _ in range(pCount):
            p = FxcPassParam()
            p.read(br)
            self.Params.append(p)

    def write(self, bw):
        for idx in self.Indices: bw.write_byte(idx)
        bw.write_byte(len(self.Params))
        for p in self.Params: p.write(bw)

class FxcTechnique:
    def read(self, br):
        self.Name = br.read_string()
        pCount = br.read_byte()
        self.Passes = []
        for _ in range(pCount):
            p = FxcPass()
            p.read(br)
            self.Passes.append(p)
    
    def write(self, bw):
        bw.write_string(self.Name)
        bw.write_byte(len(self.Passes))
        for p in self.Passes: p.write(bw)

# --- Main File Class ---

class FxcFile:
    def __init__(self):
        self.VertexType = 0
        self.PresetParams = []
        self.ShaderGroups = [] # 6 groups
        self.CBuffers1 = []
        self.Variables1 = []
        self.CBuffers2 = []
        self.Variables2 = []
        self.Techniques = []

    def load(self, data):
        br = BinaryReader(data)
        magic = br.read_uint32()
        if magic != 1702389618: # rgxe
            raise ValueError("Invalid FXC magic")
        
        self.VertexType = br.read_uint32()
        
        # Presets
        cnt = br.read_byte()
        self.PresetParams = []
        for _ in range(cnt):
            p = FxcPresetParam()
            p.read(br)
            self.PresetParams.append(p)
            
        # Shader Groups (VS, PS, CS, DS, GS, HS)
        self.ShaderGroups = []
        for i in range(6):
            g = FxcShaderGroup()
            g.read(br, i)
            self.ShaderGroups.append(g)

        # CBuffers 1
        cnt = br.read_byte()
        self.CBuffers1 = []
        for _ in range(cnt):
            c = FxcCBuffer()
            c.read(br)
            self.CBuffers1.append(c)
            
        # Variables 1
        cnt = br.read_byte()
        self.Variables1 = []
        for _ in range(cnt):
            v = FxcVariable()
            v.read(br)
            self.Variables1.append(v)
            
        # CBuffers 2
        cnt = br.read_byte()
        self.CBuffers2 = []
        for _ in range(cnt):
            c = FxcCBuffer()
            c.read(br)
            self.CBuffers2.append(c)
            
        # Variables 2
        cnt = br.read_byte()
        self.Variables2 = []
        for _ in range(cnt):
            v = FxcVariable()
            v.read(br)
            self.Variables2.append(v)
            
        # Techniques
        cnt = br.read_byte()
        self.Techniques = []
        for _ in range(cnt):
            t = FxcTechnique()
            t.read(br)
            self.Techniques.append(t)

    def save(self):
        bw = BinaryWriter()
        bw.write_uint32(1702389618) # rgxe
        bw.write_uint32(self.VertexType)
        
        bw.write_byte(len(self.PresetParams))
        for p in self.PresetParams: p.write(bw)
        
        for i in range(6):
            self.ShaderGroups[i].write(bw, i)
            
        bw.write_byte(len(self.CBuffers1))
        for c in self.CBuffers1: c.write(bw)
        
        bw.write_byte(len(self.Variables1))
        for v in self.Variables1: v.write(bw)
        
        bw.write_byte(len(self.CBuffers2))
        for c in self.CBuffers2: c.write(bw)
        
        bw.write_byte(len(self.Variables2))
        for v in self.Variables2: v.write(bw)
        
        bw.write_byte(len(self.Techniques))
        for t in self.Techniques: t.write(bw)
        
        return bw.get_data()