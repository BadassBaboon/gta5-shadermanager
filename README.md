# GTA V Shader Manager

A comprehensive GUI tool for modifying GTA V (RAGE engine) shaders. Handles the entire modding lifecycle: extracting from game archives, decompiling to HLSL, editing, recompiling, and repacking.

Supports both **GTA 5 Legacy (DX11)** and **GTA 5 Enhanced (DX12)**.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![DX11](https://img.shields.io/badge/DX11-Shader%20Model%205.0-green) ![DX12](https://img.shields.io/badge/DX12-Shader%20Model%206.x-red)

## Fork notes:
This fork keeps the upstream repository's GTA 5 shader editing features intact but also adds support for Red Dead Redemption 1 (PC) `.fxc` shader editing. Initial `.fxc` editing was made possible thanks to [Clippy95](https://github.com/Clippy95) for discovering headers and symbols so the manager can work with Red Dead Redemption 1's shaders in the first place.

---

## Features

### 🔧 Compile & Decompile
- Compile `.hlsl` source files to `.cso` shader binaries
- Decompile `.cso` binaries back to editable HLSL code
- Auto-detect shader type (VS/PS/CS/DS/GS/HS) from filename, folder, or content analysis
- **Auto-Compile (Watch Mode)** — saves in your editor instantly trigger recompilation
- **Global Defines Manager** — bulk-edit `#define` values across all shader files
- View disassembled shader bytecode (ASM)
- Auto-organize source files into subfolders by shader type

### 📦 FXC Archives (DX11)
- Parse and browse GTA V's proprietary `.fxc` shader archive format
- Unpack archives into individual `.cso` shader binaries
- Repack modified shaders back into `.fxc` (only changed shaders are injected)
- Import/Export individual shader bytecode
- View shader metadata: variables, constant buffers, version info

### 📦 AWC Archives (DX12 / FXDB)
- Parse and browse `.awc` (FXDB / SGD2 — Shader Group Data v2) shader libraries
- Full register binding metadata: CBVs, textures, samplers, UAVs
- CBuffer layout inspection with variable names and types
- Effect tree view: shaders grouped by their owning effect, with techniques and passes
- **📦 Unpack / 📤 Repack** batch flow into `/compiled/dx12/<archive>/<effect>/<stage>/` (mirrors the FXC workflow; only changed shaders are injected on repack, with automatic `.bak`)
- Import/Export individual shader bytecode
- Decompile directly from AWC with metadata restoration
- Rebuild modified AWC files (byte-identical round-trip when nothing is mutated)
- Group shaders by Effect or by Family heuristic

### 📖 Built-in Documentation
- Step-by-step modding guides for DX11 and DX12 workflows
- Edit safety warnings (register bindings, semantics, decompiler artifacts)
- Troubleshooting guide for common issues (black screen, pink textures, crashes)

---

## Requirements

```bash
pip install ttkbootstrap
```

> `ttkbootstrap` is optional but strongly recommended for the modern UI theme. The app falls back to standard `tkinter` if not installed.

### DX Compilers (included in `dxcompilers/`)

| Tool | Purpose | Source |
|------|---------|--------|
| `fxc.exe` | DX11 HLSL compiler (SM 5.0) | [Windows SDK](https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/) |
| `dxc.exe` + `dxcompiler.dll` + `dxil.dll` | DX12 HLSL compiler (SM 6.x) | [Microsoft DirectXShaderCompiler](https://github.com/microsoft/DirectXShaderCompiler) |
| `cmd_Decompiler.exe` | DX11 shader decompiler | [HLSLDecompiler](https://github.com/bo3b/3Dmigoto) |
| `decomp.exe` | DX12 shader decompiler | [RenoDX](https://github.com/clshortfuse/renodx) |
| `dxil-spirv.exe` | DXIL → SPIR-V converter | [dxil-spirv](https://github.com/HansKristian-Work/dxil-spirv) |
| `spirv-cross.exe` | SPIR-V → HLSL converter | [SPIRV-Cross](https://github.com/KhronosGroup/SPIRV-Cross) |

---

## Quick Start

Run from the shadermanager root (data folders — `dxcompilers/`, `source/`, etc. — live here; the code lives in `src/`):

```bash
python src/main.py
```

### Layout
```
src/
  main.py            entry point
  core/              config.py, defines.py, fxc_parser.py
  ui/                manual.py, tooltips.py
  tools/             export_shader_effects.py, rebuild_awc_from_live.py, verify_renodx_hash.py
  awclib/            AWC parse/write/inject package
```

---

## Folder Structure

The tool auto-creates these directories:

```
├── source/
│   ├── dx11/          ← Your editable HLSL source files (DX11)
│   └── dx12/          ← Your editable HLSL source files (DX12)
├── compiled/
│   ├── dx11/          ← Compiled .cso output (DX11)
│   └── dx12/          ← Compiled .cso output (DX12)
├── decompiled/
│   ├── dx11/          ← Decompiled shader output (read-only reference)
│   └── dx12/
├── fxc_files/         ← Place DX11 game .fxc archives here
├── awc_files/         ← Place DX12 game .awc archives here
├── dxcompilers/       ← Compiler/decompiler executables
└── settings.ini       ← Auto-generated configuration
```

---

## DX11 Modding Workflow

1. **Extract** — Use OpenIV/CodeWalker to get `.fxc` files from `update/update.rpf → common/shaders/win32_40_final/`
2. **Unpack** — FXC Archives tab → Select `.fxc` → Click **📦 Unpack**
3. **Decompile** — Compile & Decompile tab → Select `.cso` → Click **▼ Decompile to HLSL**
4. **Edit** — Move `.hlsl` to `/source/dx11/`, edit in your text editor
5. **Compile** — Select `.hlsl` → Click **▶ Compile Selected**
6. **Repack** — FXC Archives tab → Click **📤 Repack** (auto-injects changed shaders)
7. **Install** — Replace `.fxc` in game archives via OpenIV/CodeWalker

## DX12 Modding Workflow (AWC / FXDB)

1. **Extract** — Use CodeWalker (Enhanced support required) to get `sga_*.awc` files from `update/update.rpf → common/shaders/win32_60_final/`
2. **Switch** — Toggle the tool to **DX12** mode in the toolbar
3. **Unpack** — AWC Archives tab → Select `.awc`(s) → Click **📦 Unpack**
   - Output: `/compiled/dx12/<archive>.awc/<effect>/<stage>/<shader_name>.cso`
4. **Decompile** — Compile & Decompile tab → Select `.cso` → Click **▼ Decompile to HLSL**
5. **Edit** — Move `.hlsl` to `/source/dx12/`, edit in your text editor
6. **Compile** — Select `.hlsl` → Click **▶ Compile Selected** (auto-routes the `.cso` back into the matching effect folder)
7. **Repack** — AWC Archives tab → Click **📤 Repack** (only changed shaders are injected; `.bak` is created in `/awc_files/backups/`)
8. **Install** — Replace `.awc` in `update.rpf` via CodeWalker (Edit Mode required)

For single-shader edits, you can skip Unpack/Repack and use **📥 Import CSO** / **💾 Save .awc As...** on the loaded library instead.

---

## Smart Compiling

The tool uses naming conventions to auto-route compiled output:

```
source/dx11/vehicle/PS/my_shader.hlsl  →  compiled/dx11/vehicle/PS/my_shader.cso
```

Shader type is detected from:
- Folder name (`VS/`, `PS/`, `CS/`, etc.)
- Filename patterns (`_vs`, `_ps`, `.vs_6_0.hlsl`)
- Content analysis (`SV_Position`, `SV_Target` semantics)

---

## Configuration

Edit `settings.ini` to customize:

```ini
[Paths]
editor_path = C:\Program Files\Notepad++\notepad++.exe

[Window]
width = 1300
height = 900
theme = solar
show_welcome_banner = true
```

---

## Decompiler Comparison (DX12)

| Feature | decomp.exe | dxil-spirv + spirv-cross |
|---------|-----------|------------------------|
| Variable names | ✅ Preserved | ❌ Generic (`_15_m0[6]`) |
| Code readability | ✅ Clean `main()` | ❌ Wrapped in proxy structs |
| Success rate | 🟡 Medium | ✅ Higher |
| Metadata restoration | ✅ Full (via AWC tab) | ❌ N/A |
| Best for | Primary decompilation | Fallback when decomp fails |

---

## ⚠ Important Modding Warnings

1. **Do NOT change register bindings** (`register(b#)`, `register(t#)`) — the engine binds data to specific slots
2. **Do NOT modify input/output semantics** (`SV_Position`, `TEXCOORD0`, etc.) — mismatches crash the game
3. **Append new code at the end** of the main function rather than rewriting decompiled assembly-like code
4. **Always backup** before repacking — the tool creates `.bak` files automatically

---

## Credits

- FXC binary format based on [CodeWalker](https://github.com/dexyfex/CodeWalker) structures
- DX12 compiler: [Microsoft DirectXShaderCompiler](https://github.com/microsoft/DirectXShaderCompiler) (MIT License)
- SPIR-V tools: [dxil-spirv](https://github.com/HansKristian-Work/dxil-spirv) (LGPL), [SPIRV-Cross](https://github.com/KhronosGroup/SPIRV-Cross) (Apache 2.0)
- DX11 decompiler: [3Dmigoto/HLSLDecompiler](https://github.com/bo3b/3Dmigoto)
- DX12 decompiler: [RenoDX](https://github.com/clshortfuse/renodx)

## License

MIT License
