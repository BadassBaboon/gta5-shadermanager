import tkinter as tk
from tkinter import TOP, BOTTOM, LEFT, RIGHT, BOTH, X, Y, END
from tkinter.scrolledtext import ScrolledText 
import traceback

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    BaseToplevel = ttk.Toplevel
except ImportError:
    import tkinter.ttk as ttk
    BaseToplevel = tk.Toplevel

class ManualWindow(BaseToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Help & Documentation")
        self.geometry("1100x850")
        
        header_lbl = ttk.Label(self, text="📖 Help & Documentation", font=("Segoe UI", 18, "bold"))
        try:
            header_lbl.configure(bootstyle="primary")
        except: pass
        header_lbl.pack(fill=X, padx=20, pady=(20, 10))
        
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill=BOTH, expand=True, padx=20, pady=20)
        
        self._safe_add_tab("Getting Started", self._text_intro)
        self._safe_add_tab("Complete Workflow", self._text_workflow)
        self._safe_add_tab("Compile & Decompile", self._text_dev)
        self._safe_add_tab("DX11 vs DX12", self._text_comparison)
        self._safe_add_tab("FXC Archives", self._text_fxc)
        self._safe_add_tab("AWC Archives", self._text_awc)
        self._safe_add_tab("Smart Compiling", self._text_smart_compile)
        self._safe_add_tab("Global Defines", self._text_defines)
        self._safe_add_tab("Shortcuts & Tips", self._text_shortcuts)
        self._safe_add_tab("Troubleshooting", self._text_trouble)

    def _safe_add_tab(self, title, content_func):
        try:
            content = content_func()
            self._add_tab(title, content)
        except Exception as e:
            print(f"Error creating tab {title}: {e}")
            traceback.print_exc()

    def _add_tab(self, title, content):
        frame = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(frame, text=title)
        
        text_area = ScrolledText(frame, font=("Segoe UI", 10), wrap=tk.WORD, bd=0, highlightthickness=0)
        text_area.pack(fill=BOTH, expand=True, padx=5, pady=5)
        
        text_area.tag_configure("h1", font=("Segoe UI", 14, "bold"), foreground="#268bd2", spacing3=15, spacing1=5)
        text_area.tag_configure("h2", font=("Segoe UI", 11, "bold"), foreground="#2aa198", spacing3=10, spacing1=2)
        text_area.tag_configure("body", spacing1=2, spacing2=2)
        text_area.tag_configure("bold", font=("Segoe UI", 10, "bold"))
        text_area.tag_configure("code", font=("Consolas", 10), background="#002b36", foreground="#859900", lmargin1=20)
        text_area.tag_configure("warn", foreground="#dc322f", font=("Segoe UI", 10, "bold"))
        text_area.tag_configure("list", lmargin1=20, lmargin2=20)
        text_area.tag_configure("tip", foreground="#859900", font=("Segoe UI", 10, "italic"))

        self._insert_formatted(text_area, content)
        text_area.configure(state='disabled')

    def _insert_formatted(self, widget, content):
        for item in content:
            tag = item[0]
            text = item[1]
            widget.insert(END, text + "\n", tag)

    # ====================================================================
    # CONTENT
    # ====================================================================

    def _text_intro(self):
        return [
            ("h1", "Welcome to GTA V Shader Manager"),
            ("body", "This tool is a comprehensive environment for modifying GTA V (RAGE engine) shaders. It handles the entire lifecycle: extracting from game archives, decompiling to readable HLSL, editing, recompiling, and repacking."),
            ("body", ""),
            ("bold", "If you're new, start with the 'Complete Workflow' tab for a step-by-step guide."),
            ("body", ""),

            ("h1", "What Can This Tool Do?"),
            ("list", "• Compile .hlsl source files to .cso shader binaries"),
            ("list", "• Decompile .cso binaries back to editable .hlsl code"),
            ("list", "• Unpack and repack DX11 .fxc shader archives"),
            ("list", "• Browse and modify DX12 .awc shader libraries"),
            ("list", "• Auto-compile shaders on file save (watch mode)"),
            ("list", "• Bulk-edit #define values across all shader files"),
            ("list", "• View disassembled shader bytecode (ASM)"),
            ("body", ""),

            ("h1", "Folder Structure"),
            ("body", "The tool uses these folders (created automatically):"),
            ("code", "  /source/dx11/     Your editable .hlsl source files (DX11)"),
            ("code", "  /source/dx12/     Your editable .hlsl source files (DX12)"),
            ("code", "  /compiled/dx11/   Compiled .cso output (DX11)"),
            ("code", "  /compiled/dx12/   Compiled .cso output (DX12)"),
            ("code", "  /decompiled/      Decompiled shader output (read-only reference)"),
            ("code", "  /fxc_files/       Place DX11 game .fxc archives here"),
            ("code", "  /awc_files/       Place DX12 game .awc archives here"),
            ("code", "  /dxcompilers/     Required compiler tools (fxc.exe, dxc.exe, etc.)"),
            ("body", ""),

            ("h1", "Required Tools"),
            ("body", "Place these executables in the /dxcompilers/ folder:"),
            ("list", "• fxc.exe — DX11 HLSL compiler (from Windows SDK)"),
            ("list", "• cmd_Decompiler.exe — DX11 shader decompiler"),
            ("list", "• dxc.exe — DX12 HLSL compiler (Shader Model 6.0+)"),
            ("list", "• dxil-spirv.exe + spirv-cross.exe — DX12 SPIR-V decompilation pipeline"),
            ("list", "• decomp.exe — Fallback DX12 decompiler"),
        ]

    def _text_workflow(self):
        return [
            ("h1", "DX11 Step-by-Step Modding Guide"),
            ("body", "Follow this guide to extract, edit, and replace shaders in the game."),

            ("h2", "Step 1: Get the Shader Archives"),
            ("list", "1. Open OpenIV or CodeWalker."),
            ("list", "2. Navigate to update/update.rpf or update/update2.rpf."),
            ("list", "3. Find the folder 'win32_40_final' (usually inside common/shaders)."),
            ("list", "4. Extract the desired .fxc files (e.g. 'vehicle.fxc') into this tool's /fxc_files/ folder."),

            ("h2", "Step 2: Unpack the Archive"),
            ("list", "1. Open this tool and go to the 'FXC Archives' tab (sidebar)."),
            ("list", "2. Your .fxc files should appear in the left panel. If not, click ⟳ Refresh."),
            ("list", "3. Select the file and click 📦 Unpack."),
            ("body", "This extracts individual .cso shader binaries into /compiled/dx11/<archive_name>/."),

            ("h2", "Step 3: Decompile to Editable HLSL"),
            ("list", "1. Go to the 'Compile & Decompile' tab (sidebar)."),
            ("list", "2. Look at the right panel (Compiled Output). Find your unpacked archive folder."),
            ("list", "3. Select the .cso file(s) you want to edit."),
            ("list", "4. Click ▼ Decompile to HLSL."),
            ("body", "A .hlsl file will appear in the /decompiled/ folder."),

            ("h2", "Step 4: Edit the Shader"),
            ("list", "1. Move (or copy) the .hlsl file from /decompiled/ to /source/dx11/ for editing."),
            ("list", "2. Switch File View to 'Workspace' in the toolbar."),
            ("list", "3. Open the file in your text editor (double-click or use 📝 Open in Editor)."),
            ("list", "4. Make your HLSL modifications."),
            ("tip", "Tip: Enable 'Auto-Compile (Watch)' to recompile instantly on save."),

            ("h2", "Step 5: Compile"),
            ("list", "1. Select the modified .hlsl file in the left panel."),
            ("list", "2. Click ▶ Compile Selected."),
            ("body", "The compiled .cso is placed back in the correct /compiled/ subfolder automatically."),

            ("h2", "Step 6: Repack and Install"),
            ("list", "1. Go to the 'FXC Archives' tab."),
            ("list", "2. Select the original .fxc file."),
            ("list", "3. Click 📤 Repack. Only changed shaders are injected; a backup is created."),
            ("list", "4. Replace the original .fxc in the game archive using OpenIV/CodeWalker."),
            ("body", ""),

            ("h1", "DX12 Workflow (AWC)"),
            ("body", "DX12 shaders use .awc archives instead of .fxc."),
            ("h2", "Step 1: Prepare"),
            ("list", "1. Switch the tool to DX12 mode using the DirectX Version toggle."),
            ("list", "2. The sidebar will show 'AWC Archives' instead of 'FXC Archives'."),

            ("h2", "Step 2: Browse and Export"),
            ("list", "1. Place .awc files in /awc_files/ or use 📂 Open .awc File."),
            ("list", "2. Select a shader in the tree and click 📤 Export CSO to extract it."),

            ("h2", "Step 3: Decompile, Edit, Compile"),
            ("list", "1. Click ▼ Decompile to HLSL on the AWC page to get editable source."),
            ("list", "2. Edit the .hlsl and compile via the 'Compile & Decompile' tab (DX12 mode)."),

            ("h2", "Step 4: Import and Save"),
            ("list", "1. Go back to the AWC page, select the shader you modified."),
            ("list", "2. Click 📥 Import CSO and select your compiled .cso."),
            ("list", "3. Click 💾 Save .awc As... to write the modified AWC."),
            ("body", ""),

            ("h1", "⚠ Critical Warnings for Editing"),
            ("warn", "Read this to avoid crashing the game!"),

            ("bold", "1. Input/Output Signatures (Semantics):"),
            ("body", "Do NOT change the input/output structures (struct VS_INPUT, PS_OUTPUT, etc.) unless rewriting the entire pipeline. The engine expects specific data at specific slots. Changing 'TEXCOORD0' to 'TEXCOORD1' will crash the game or produce black textures."),

            ("bold", "2. Register Bindings (register(b#), register(t#)):"),
            ("body", "Decompiled code has specific bindings like 'cbuffer cb0 : register(b0)'. NEVER change these register numbers. The game engine binds data (camera position, time, light color) to these specific slots."),

            ("bold", "3. Decompiler Artifacts:"),
            ("body", "Decompiled code is messy by nature (variables like 'r0.x', 'v1.y'). It's often safest to append your new code at the end of the main function rather than rewriting generated assembly-like code."),
        ]

    def _text_dev(self):
        return [
            ("h1", "Compile & Decompile Page"),
            ("body", "This is your main workspace, split into two panels: Source Files (left) and Compiled Output (right)."),
            ("body", ""),

            ("h2", "Toolbar Controls"),
            ("bold", "DirectX Version (DX11 / DX12):"),
            ("body", "  Switches between DirectX 11 (Shader Model 5.0, fxc.exe) and DirectX 12 (Shader Model 6.0, dxc.exe). Changes which folders are active and which archive manager is available."),
            ("bold", "Decompile Method (DX12 only):"),
            ("body", "  'SPIR-V' uses a two-step pipeline (dxil-spirv → spirv-cross) for cleaner output. 'Decomp.exe' uses a direct decompiler as a fallback."),
            ("bold", "File View (Workspace / Decompiled):"),
            ("body", "  'Workspace' shows your /source/ folder — your editable files. 'Decompiled' shows the /decompiled/ folder — read-only reference from decompilation."),
            ("bold", "⟳ Refresh All:"),
            ("body", "  Reloads all file lists across every tab."),
            ("bold", "Auto-Compile (Watch):"),
            ("body", "  When enabled, the tool monitors your /source/ folder and automatically recompiles any .hlsl file when it detects a save."),
            ("body", ""),

            ("h2", "Left Panel — Source Files"),
            ("bold", "Filter:"),
            ("body", "  Type in the search box to filter shaders by filename."),
            ("bold", "Select All:"),
            ("body", "  Selects every file in the tree."),
            ("bold", "▶ Compile Selected:"),
            ("body", "  Compiles the selected .hlsl file(s) into .cso binaries. Output goes to the /compiled/ folder, automatically placed in the correct subfolder."),
            ("bold", "⚙ Global Defines:"),
            ("body", "  Opens the Define Manager to bulk-edit #define values across all .hlsl files."),
            ("bold", "📂 Organize Files:"),
            ("body", "  Auto-sorts source files into subfolders based on naming conventions and shader type (VS, PS, CS, etc.)."),
            ("bold", "📝 Open in Editor:"),
            ("body", "  Opens the selected file in your configured text editor (set editor_path in settings.ini) or the system default."),
            ("body", ""),

            ("h2", "Right Panel — Compiled Output"),
            ("bold", "Select All:"),
            ("body", "  Selects all .cso files."),
            ("bold", "📜 View ASM:"),
            ("body", "  Disassembles the selected .cso and opens a window with the raw shader assembly instructions."),
            ("bold", "▼ Decompile to HLSL:"),
            ("body", "  Decompiles selected .cso file(s) back to .hlsl source in the /decompiled/ folder."),
            ("body", ""),

            ("h2", "Context Menu (Right-Click)"),
            ("body", "  Right-click any file in either panel to access:"),
            ("list", "• Open File Location — opens the file's folder in Explorer"),
            ("list", "• Open in Editor — opens the file in your text editor"),
            ("list", "• View ASM Code — (compiled panel only) disassemble the .cso"),
        ]

    def _text_comparison(self):
        return [
            ("h1", "DirectX 11 vs DirectX 12"),
            ("body", "The tool supports both APIs, but they use completely different toolchains and archive formats."),

            ("h2", "Feature Comparison"),
            ("list", "• Source Editing:    Works for BOTH"),
            ("list", "• Auto-Compile:     Works for BOTH"),
            ("list", "• Decompilation:    Works for BOTH (different tools)"),
            ("list", "• ASM View:         Works for BOTH"),
            ("list", "• FXC Archives:     DX11 ONLY"),
            ("list", "• AWC Archives:     DX12 ONLY"),

            ("h2", "DirectX 11 (Legacy)"),
            ("bold", "Compiler:"),
            ("body", "  fxc.exe (Shader Model 5.0)"),
            ("bold", "Decompiler:"),
            ("body", "  cmd_Decompiler.exe"),
            ("bold", "Archives:"),
            ("body", "  .fxc files — managed via the FXC Archives tab. Supports unpack/repack."),

            ("h2", "DirectX 12 (Next-Gen)"),
            ("bold", "Compiler:"),
            ("body", "  dxc.exe (Shader Model 6.0+). Name files with .vs_6_0.hlsl, .ps_6_0.hlsl, etc. to set the target profile."),
            ("bold", "Decompiler:"),
            ("body", "  decomp.exe (Primary) OR dxil-spirv + spirv-cross pipeline."),
            ("bold", "Archives:"),
            ("body", "  .awc files — managed via the AWC Archives tab. Supports import/export/rebuild."),

            ("h2", "HLSL Code Differences (SM 5.0 vs SM 6.0)"),
            ("bold", "Syntax:"),
            ("body", "  DX12 (SM 6.0) uses the DXC compiler which is stricter and supports newer C++-like syntax."),
            ("list", "• Wave Intrinsics: WaveActiveSum, WaveReadLaneFirst, etc."),
            ("list", "• 64-bit Integers: Full support in SM 6.0."),
            ("list", "• Resources: In SM 5.1+, resources can be unbounded arrays."),

            ("bold", "Root Signatures & Spaces:"),
            ("body", "  DX12 shaders use 'register(t0, space1)'. The 'space' keyword is critical in DX12 binding; DX11 only used registers."),
            ("warn", "If porting DX11 code to DX12, check if any 'space' arguments are required by the engine."),
        ]

    def _text_fxc(self):
        return [
            ("h1", "FXC Archives"),
            ("body", "GTA V stores DX11 shaders in .fxc archives. This tab lets you unpack and repack them."),
            ("body", ""),

            ("h2", "Workspace Panel (Left)"),
            ("body", "Shows all .fxc files found in /fxc_files/. Click a file to load it and see its shaders."),
            ("body", "Double-click a file to unpack it directly."),
            ("body", ""),

            ("h2", "Shader Browser (Middle)"),
            ("body", "After loading an FXC, shaders are grouped by type (VS, PS, CS, etc.). Select a shader to see its details in the right panel."),
            ("body", "Use the filter bar to search by shader name."),
            ("body", ""),

            ("h2", "Button Reference"),
            ("bold", "📦 Unpack:"),
            ("body", "  Extracts all shaders from the selected .fxc archive(s) into /compiled/dx11/<archive_name>/ as individual .cso files."),
            ("bold", "📤 Repack:"),
            ("body", "  Reads the unpacked folder, compares each .cso with the original bytecode, and injects only changed shaders back into the .fxc. A backup is created automatically in /fxc_files/backups/."),
            ("bold", "💾 Save FXC:"),
            ("body", "  Saves the currently loaded (and possibly modified) FXC to a new file. Use this after importing CSO data via the Import button."),
            ("bold", "📥 Import CSO:"),
            ("body", "  Replaces the selected shader's bytecode with a .cso file from disk. The change is in-memory only — you must Save FXC afterwards."),
            ("bold", "📤 Export CSO:"),
            ("body", "  Exports the selected shader(s) as individual .cso files to /compiled/dx11/<archive_name>/<type>/."),
            ("body", ""),

            ("h2", "Repacking Logic (How It Works)"),
            ("body", "When you click Repack, the tool does NOT rebuild from scratch. It:"),
            ("list", "1. Reads the original .fxc structure"),
            ("list", "2. Checks the corresponding folder in /compiled/"),
            ("list", "3. Compares bytecode — only injects shaders that changed"),
            ("list", "4. Creates a .bak backup before overwriting"),
            ("tip", "If you compiled a shader but the logic is identical, the binary won't change and the tool reports 'No changes'."),
        ]

    def _text_awc(self):
        return [
            ("h1", "AWC Archives"),
            ("body", "DX12 (Next-Gen) GTA V uses .awc shader library files instead of .fxc archives. This tab lets you browse, modify, and rebuild them."),
            ("body", ""),

            ("h2", "Loading AWC Files"),
            ("body", "Place .awc files in /awc_files/ and they will appear in the left panel. Or click '📂 Open .awc File' to load from any location."),
            ("body", ""),

            ("h2", "Shader Browser (Middle)"),
            ("body", "After loading an AWC, shaders are grouped by type (VS, PS, CS, GS, HS, DS). Each entry shows the shader hash/name, type, and binary size."),
            ("bold", "Filter:"),
            ("body", "  Type to filter shaders by name or hash."),
            ("bold", "Group by Technique ID:"),
            ("body", "  Toggle this to group shaders by their technique hash (extracted from the shader name). Useful for finding related VS/PS pairs."),
            ("body", ""),

            ("h2", "Shader Details (Right)"),
            ("body", "Select a shader to view its metadata:"),
            ("list", "• Name and hash"),
            ("list", "• Binary size and wave size"),
            ("list", "• Register bindings (CBVs, textures, samplers, UAVs)"),
            ("list", "• Constant buffer layouts"),
            ("body", ""),

            ("h2", "Button Reference"),
            ("bold", "📂 Open .awc File:"),
            ("body", "  Open an .awc shader library from any location on disk."),
            ("bold", "💾 Save .awc As...:"),
            ("body", "  Rebuild the loaded AWC with any modifications and save to a new file."),
            ("bold", "📥 Import CSO:"),
            ("body", "  Replace the selected shader's bytecode with a compiled .cso file. The change is in-memory — Save afterwards."),
            ("bold", "📤 Export CSO:"),
            ("body", "  Export the selected shader's binary data as a .cso file."),
            ("bold", "▼ Decompile to HLSL:"),
            ("body", "  Decompile the shader to .hlsl source code using decomp.exe or the SPIR-V pipeline. Output goes to /decompiled/."),
            ("body", ""),
            
            ("h2", "Decompilers: decomp.exe vs SPIR-V Pipeline"),
            ("warn", "⚠ Important: Reverse-engineering shaders is imperfect. Neither decompiler will work in 100% of cases."),
            ("body", "The tool supports two different decompilers for DX12 AWC shaders. They produce vastly different code:"),
            ("bold", "1. decomp.exe + AWC Metadata Restoration (Recommended):"),
            ("body", "  • Note: decomp.exe alone only restores metadata that the shader actively uses, hiding the rest."),
            ("body", "  • IN THIS APP: When decompiling from the AWC Archives tab, the app reads the raw AWC metadata and artificially restores all missing shader info/registers back into the HLSL."),
            ("body", "  • Preserves real constant buffer variable names (e.g., 'cb9_space1_003x') and clear 'main' definitions."),
            ("body", "  • Provides a complete, highly readable structure perfect for editing."),
            ("body", ""),
            ("bold", "2. dxil-spirv + spirv-cross (Fallback):"),
            ("body", "  • Has a higher success rate than decomp.exe (can successfully decompile a greater amount of shaders)."),
            ("body", "  • Strips original variable names entirely, replacing them with generic arrays (e.g., 'float4 _15_m0[6]')."),
            ("body", "  • Wraps the entry point in generated 'SPIRV_Cross_Input' structs and proxy 'frag_main()' functions."),
            ("body", "  • Heavily obfuscated and very difficult to read or modify manually."),
            ("body", "  • Use only if decomp.exe fails to parse a specific shader."),
            ("body", ""),

            ("h2", "Update Metadata Toggle"),
            ("warn", "⚠ Leave this OFF unless you know what you're doing."),
            ("body", "When enabled, shader metadata (register bindings, sizes) is recalculated when importing a CSO. This is necessary if you change the shader's interface (add/remove registers), but can cause game crashes if the metadata doesn't match the engine's expectations."),
            ("body", "For simple edits that don't change the shader interface, leave it OFF."),
        ]

    def _text_smart_compile(self):
        return [
            ("h1", "Smart Compiling"),
            ("body", "The tool uses file naming conventions and folder structure to automatically determine where compiled output should go."),
            ("body", ""),

            ("h2", "Naming Convention"),
            ("bold", "Simple:"),
            ("code", "  folder+name.hlsl  →  /compiled/<dx>/folder/PS/name.cso"),
            ("body", ""),
            ("bold", "With Hash:"),
            ("code", "  349079745+group+name.hlsl"),
            ("body", "If the filename starts with a hash (from decompilation), the tool looks up hash.txt and copies the compiled output to ALL locations that reference this shader hash."),
            ("body", ""),

            ("h2", "Folder-Based Organization"),
            ("body", "If your file is already in a subfolder (e.g. /source/dx11/vehicle/PS/my_shader.hlsl), the tool uses the folder path to determine output location. Shader type is detected from:"),
            ("list", "• Folder name (VS, PS, CS, DS, GS, HS)"),
            ("list", "• Filename conventions (_vs, _ps, +vs, +ps, etc.)"),
            ("list", "• File content analysis (SV_Position, SV_Target semantics)"),
            ("body", ""),

            ("h2", "The 📂 Organize Files Button"),
            ("body", "Click this to automatically reorganize flat source files into subfolders based on their naming pattern. For example:"),
            ("code", "  vehicle+PS+my_pixel_shader.hlsl"),
            ("body", "becomes:"),
            ("code", "  /vehicle/PS/my_pixel_shader.hlsl"),
        ]

    def _text_defines(self):
        return [
            ("h1", "Global Defines Manager"),
            ("body", "A tool for bulk-editing '#define' values across multiple .hlsl files at once."),
            ("body", ""),

            ("h2", "How It Works"),
            ("list", "1. Click '⚙ Global Defines' on the Compile & Decompile page."),
            ("list", "2. The tool scans all .hlsl files in your current /source/ directory."),
            ("list", "3. It collects every '#define NAME value' line and lists them."),
            ("list", "4. Select a parameter, enter a new value, and click 'Apply to ALL Files'."),
            ("body", ""),

            ("h2", "Columns"),
            ("bold", "Parameter:"),
            ("body", "  The #define name (e.g. QUALITY_LEVEL, ENABLE_SHADOWS)."),
            ("bold", "Value:"),
            ("body", "  The current value. Shows *MIXED* if different files have different values."),
            ("bold", "Files:"),
            ("body", "  Number of files containing this define."),
            ("body", ""),

            ("h2", "Use Cases"),
            ("list", "• Changing global quality settings (QUALITY_LEVEL, SHADOW_SAMPLES)"),
            ("list", "• Toggling features on/off (ENABLE_SHADOWS, USE_HDR)"),
            ("list", "• Tuning parameters across many shaders simultaneously"),
            ("body", ""),
            ("warn", "Only simple '#define NAME value' lines are detected. Macro functions like '#define FOO(x)' are ignored."),
        ]

    def _text_shortcuts(self):
        return [
            ("h1", "Keyboard Shortcuts & Tips"),
            ("body", ""),

            ("h2", "Mouse Interactions"),
            ("bold", "Double-click a source file:"),
            ("body", "  Opens it in your text editor."),
            ("bold", "Double-click an FXC archive:"),
            ("body", "  Unpacks it immediately."),
            ("bold", "Right-click any file:"),
            ("body", "  Opens a context menu with 'Open File Location' and 'Open in Editor'."),
            ("bold", "Right-click a compiled .cso:"),
            ("body", "  Context menu also includes 'View ASM Code'."),
            ("body", ""),

            ("h2", "Multi-Selection"),
            ("body", "Hold Ctrl or Shift to select multiple files in any tree view. Most actions (Compile, Decompile, Export, Unpack) work on multiple selections."),
            ("body", ""),

            ("h2", "Workflow Tips"),
            ("tip", "Tip: Use Auto-Compile (Watch) for rapid iteration — edit in your text editor, save, and the tool recompiles instantly."),
            ("tip", "Tip: Keep the Terminal/Log panel visible to catch compile errors early."),
            ("tip", "Tip: Use 'Select All' then 'Compile Selected' to batch-compile all shaders at once."),
            ("tip", "Tip: When modifying multiple shaders from the same .fxc, unpack once, edit all, then repack once at the end."),
            ("body", ""),

            ("h2", "Configuration (settings.ini)"),
            ("body", "You can customize the tool by editing settings.ini:"),
            ("bold", "editor_path:"),
            ("body", "  Path to your preferred text editor (e.g. C:\\\\Program Files\\\\Notepad++\\\\notepad++.exe). If empty, the system default is used."),
            ("bold", "theme:"),
            ("body", "  UI theme name (e.g. solar, darkly, cosmo). Requires ttkbootstrap."),
        ]

    def _text_trouble(self):
        return [
            ("h1", "Troubleshooting"),
            ("body", ""),

            ("h2", "General Issues"),
            ("bold", "Compiler Error / Access Denied:"),
            ("body", "Ensure fxc.exe (DX11) or dxc.exe (DX12) is in the /dxcompilers/ folder and not read-only or blocked by antivirus."),
            ("bold", "Nothing appears in the file lists:"),
            ("body", "Click ⟳ Refresh. Check that files are in the correct folders (/source/dx11/ for DX11 workspace, /fxc_files/ for FXC archives, etc.)."),
            ("body", ""),

            ("h2", "Compilation Issues"),
            ("bold", "Wrong shader profile detected:"),
            ("body", "The tool auto-detects the shader type from filename and content. If it gets it wrong, rename your file to include the profile (e.g. my_shader.ps_5_0.hlsl)."),
            ("bold", "DX12 compilation fails:"),
            ("body", "Check if your filename includes the profile (e.g. .ps_6_0.hlsl). DXC needs to know the target profile. Also ensure HLSL syntax is clean — DXC is stricter than FXC."),
            ("body", ""),

            ("h2", "FXC/Repack Issues"),
            ("bold", "Repack does nothing / 'No changes':"),
            ("body", "Ensure your compiled .cso file is in the correct subfolder (VS, PS, etc.) inside /compiled/dx11/<ArchiveName>/. The filename must match what was originally unpacked."),
            ("bold", "Game crashes after replacing .fxc:"),
            ("body", "You likely modified a semantic signature or constant buffer register. Revert to the original decompiled code and try smaller changes."),
            ("body", ""),

            ("h2", "AWC Issues"),
            ("bold", "AWC import causes game crash:"),
            ("body", "If you changed the shader's register layout, try enabling 'Update Metadata' before importing. If crashes persist, the shader may require exact binary compatibility."),
            ("bold", "Decompile fails (SPIR-V pipeline):"),
            ("body", "Try switching to 'Decomp.exe' method. Some shaders with complex buffer layouts fail the SPIR-V pipeline but work with the direct decompiler."),
            ("body", ""),

            ("h2", "Visual Issues"),
            ("bold", "Black screen in game:"),
            ("body", "You likely modified a signature or constant buffer register. Revert to the original decompiled code. It's safer to append new logic at the end of the main function."),
            ("bold", "Pink/magenta textures:"),
            ("body", "Usually means a texture sampling error. Check that texture register bindings (t0, t1, etc.) are unchanged from the original."),
        ]