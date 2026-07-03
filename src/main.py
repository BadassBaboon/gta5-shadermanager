import os
import re
import subprocess
import tkinter as tk
import shutil
import threading
import queue
import time
import traceback
import sys
import zlib


def renodx_hash(data):
    """RenoDX runtime shader hash = zlib/IEEE CRC-32 over the bytecode blob
    (reproduces RenoDX utils::hash::ComputeCRC32). Lets the GUI show the same
    0xXXXXXXXX RenoDX displays in-game, computed offline from the stored
    bytecode. NOTE: this is NOT the DXBC/DXIL container's embedded checksum,
    and NOT the AWC's 64-bit SGA hash -- it's a CRC-32 of the whole blob."""
    return f"0x{zlib.crc32(data) & 0xFFFFFFFF:08X}" if data else ""


# --- MODULES IMPORT ---
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    MODERN_UI = True
except ImportError:
    import tkinter as tk
    import tkinter.ttk as ttk
    from tkinter import TOP, BOTTOM, LEFT, RIGHT, BOTH, X, Y, END, VERTICAL, HORIZONTAL
    MODERN_UI = False
    print("WARNING: ttkbootstrap not found. Using standard tkinter.")

# Fix for capitalization in ttkbootstrap
if not hasattr(ttk, 'PanedWindow') and hasattr(ttk, 'Panedwindow'):
    ttk.PanedWindow = ttk.Panedwindow

# Import local modules with error handling
try:
    from core import fxc_parser
    from core.config import ConfigManager
    from ui.manual import ManualWindow
    from core.defines import DefineManagerWindow
    from ui.tooltips import hint
    from tkinter import filedialog, messagebox
    from awclib.parser import parse_awc_file
    from awclib.awc_writer import AWCRebuilder
    from awclib.models import AWCFile, Shader
    from awclib.decompiler import decompile_shader
    from core.semantic_fixup import fixup_semantics
    from core.cbuffer_annotate import annotate_hlsl
    from core import rdr1_fxc
except ImportError as e:
    print(f"Critical Import Error: {e}")
    print("Ensure the src/ package (core/, ui/, tools/, awclib/) is intact and run from the shadermanager root.")

# --- ASM WINDOW CLASS ---
class AsmWindow(ttk.Toplevel):
    def __init__(self, parent, filename, content):
        super().__init__(parent)
        self.title(f"ASM: {filename}")
        self.geometry("900x700")
        
        
        container = ttk.Frame(self)
        container.pack(fill=BOTH, expand=True, padx=5, pady=(5, 0))
        
        self.text_area = tk.Text(container, font=("Consolas", 10), bg="#002b36", fg="#839496", insertbackground="white", bd=0)
        self.text_area.pack(side=LEFT, fill=BOTH, expand=True)
        
        sb = ttk.Scrollbar(container, orient=VERTICAL, command=self.text_area.yview)
        sb.pack(side=RIGHT, fill=Y)
        self.text_area.config(yscrollcommand=sb.set)
        
        self.text_area.insert(END, content)
        self.text_area.config(state=tk.DISABLED)

        
        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(side=BOTTOM, fill=X)
        
        ttk.Button(btn_frame, text="Close", command=self.destroy, bootstyle="secondary").pack(side=RIGHT, padx=5)
        ttk.Button(btn_frame, text="📋 Copy All", command=self.copy_all, bootstyle="success").pack(side=RIGHT, padx=5)

    def copy_all(self):
        self.clipboard_clear()
        self.clipboard_append(self.text_area.get("1.0", END))
        self.update()  

# --- MAIN APP ---
class ShaderManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("GTA V Shader Manager v0.0.2")
        
        self.cfg_mgr = ConfigManager()
        self.config = self.cfg_mgr.load()
        
        w, h = self.config["Window"]["width"], self.config["Window"]["height"]
        self.root.geometry(f"{w}x{h}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # UI Styling
        if MODERN_UI:
            style = ttk.Style()
            style.configure("Treeview", font=("Segoe UI", 10), rowheight=30)
            style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
            style.configure("TButton", font=("Segoe UI", 10))
            style.configure("Sidebar.TButton", font=("Segoe UI", 12), anchor="w", padding=15, background="#002b36", foreground="#839496", borderwidth=0)
            style.map("Sidebar.TButton", background=[('active', '#073642'), ('selected', '#2aa198')], foreground=[('active', 'white'), ('selected', 'white')])

        # --- PATH SETUP ---
        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            # main.py now lives in src/; data folders (source, compiled, dxcompilers, ...)
            # stay at the shadermanager root, one level up from src/.
            self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        self.dirs = {
            "source": os.path.join(self.base_dir, "source"),
            "compiled": os.path.join(self.base_dir, "compiled"),
            "decompiled": os.path.join(self.base_dir, "decompiled"),
            "fxc": os.path.join(self.base_dir, "fxc_files"),
            "awc": os.path.join(self.base_dir, "awc_files"),
            "compilers": os.path.join(self.base_dir, "dxcompilers")
        }
        self.tools = {
            "fxc": os.path.join(self.base_dir, self.config["Paths"]["fxc_path"]),
            "decompiler": os.path.join(self.base_dir, self.config["Paths"]["decompiler_path"]),
            "dxc": os.path.join(self.base_dir, self.config["Paths"]["dx12_compiler_path"]),
            "dxil_spirv": os.path.join(self.base_dir, self.config["Paths"]["dxil_spirv_path"]),
            "spirv_cross": os.path.join(self.base_dir, self.config["Paths"]["spirv_cross_path"]),
            "decomp_fallback": os.path.join(self.base_dir, self.config["Paths"]["decomp_fallback_path"])
        }
        self.hash_txt = os.path.join(self.base_dir, "hash.txt")

        # State
        self.source_mode_var = tk.StringVar(value=self.config["Window"]["mode"])
        self.watcher_var = tk.BooleanVar(value=False)
        self.search_var = tk.StringVar()
        self.search_var.trace("w", self._filter_source)
        self.hash_map = {}
        self.msg_queue = queue.Queue()
        self.is_processing = False
        self.watcher_running = False
        self.current_page = None
        self.pages = {}
        self.show_welcome = self.config["Window"].get("show_welcome_banner", "true").lower() == "true"

        self.dx_version = tk.StringVar(value="dx11")  # Default to DX11
        self.decomp_method_var = tk.StringVar(value="spirv")  # Default to SPIR-V pipeline

        self._setup_fs()
        self._load_hashes()
        self._build_layout()
        
        self.root.after(200, self._process_queue)
        
        # Ensure UI state matches default values (handle hiding method selector for DX11)
        self._on_dx_change()

    def _setup_fs(self):
        # Create base directories
        for d in self.dirs.values(): os.makedirs(d, exist_ok=True)
        
        # Create per-profile subdirectories for relevant folders
        for key in ["source", "compiled", "decompiled"]:
            base = self.dirs[key]
            os.makedirs(os.path.join(base, "dx11"), exist_ok=True)
            os.makedirs(os.path.join(base, "dx12"), exist_ok=True)
            os.makedirs(os.path.join(base, "rdr1"), exist_ok=True)

    def on_close(self):
        self.watcher_running = False
        self.config["Window"]["width"] = str(self.root.winfo_width())
        self.config["Window"]["height"] = str(self.root.winfo_height())
        self.config["Window"]["mode"] = self.source_mode_var.get()
        self.cfg_mgr.save()
        self.root.destroy()

    def _get_current_dir(self, key):
        """Returns path with dx11/dx12 subfolder for source/compiled/decompiled"""
        if key in ["source", "compiled", "decompiled"]:
            version = self.dx_version.get()
            return os.path.join(self.dirs[key], version)
        return self.dirs[key]

    # --- HELPER: SCROLLABLE TREE ---
    def _create_scrolled_tree(self, parent, **kwargs):
        """Creates a Treeview with automatic vertical and horizontal scrollbars."""
        container = ttk.Frame(parent)
        
        # Scrollbars
        vsb = ttk.Scrollbar(container, orient=VERTICAL, bootstyle="secondary-round")
        hsb = ttk.Scrollbar(container, orient=HORIZONTAL, bootstyle="secondary-round")
        
        # Tree
        tree = ttk.Treeview(container, yscrollcommand=vsb.set, xscrollcommand=hsb.set, **kwargs)
        vsb.config(command=tree.yview)
        hsb.config(command=tree.xview)
        
        # Grid Layout
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        
        return container, tree

    # --- UI BUILDING ---
    def _build_layout(self):
        main_container = ttk.Frame(self.root)
        main_container.pack(fill=BOTH, expand=True)

        # Sidebar
        sidebar = ttk.Frame(main_container, bootstyle="dark", width=250)
        sidebar.pack(side=LEFT, fill=Y)
        
        lbl_brand = ttk.Label(sidebar, text="GTA V\nSHADER MANAGER", font=("Segoe UI", 18, "bold"), bootstyle="inverse-dark", justify="center")
        lbl_brand.pack(pady=(25, 5), padx=20)
        lbl_version = ttk.Label(sidebar, text="v0.0.2", font=("Segoe UI", 9), bootstyle="inverse-dark", justify="center")
        lbl_version.pack(pady=(0, 30), padx=20)

        self.btn_dev = ttk.Button(sidebar, text="🔧  Compile & Decompile", style="Sidebar.TButton", command=lambda: self._show_page("dev"))
        self.btn_dev.pack(fill=X, pady=2)
        hint(self.btn_dev, "Compile HLSL source to .cso binaries,\nor decompile .cso back to HLSL.")
        
        self.btn_fxc = ttk.Button(sidebar, text="📦  FXC Archives", style="Sidebar.TButton", command=lambda: self._show_page("fxc"))
        self.btn_fxc.pack(fill=X, pady=2)
        hint(self.btn_fxc, "Unpack and repack DX11 .fxc shader archives\nused by the game engine.")
        
        self.btn_awc = ttk.Button(sidebar, text="📦  AWC Archives", style="Sidebar.TButton", command=lambda: self._show_page("awc"))
        hint(self.btn_awc, "Unpack and repack DX12 .awc (FXDB / SGD2) shader libraries.\nBrowse effects, techniques and passes; import/export individual\nshaders or batch-extract everything by effect group.")
        # Do not pack awc button initially; handled by _on_dx_change

        self.btn_renodx = ttk.Button(sidebar, text="🔄  RenoDX Sync", style="Sidebar.TButton", command=lambda: self._show_page("renodx"))
        self.btn_renodx.pack(fill=X, pady=2)
        hint(self.btn_renodx, "Bridge between RenoDX live shaders and the .awc library.\nExport the effect map (shader_effects.json) into renodx-dev,\nand rebuild the .awc from shaders edited in the live folder.")

        ttk.Frame(sidebar, height=2, bootstyle="secondary").pack(fill=X, pady=20, padx=20)

        self.btn_help = ttk.Button(sidebar, text="📖  Help & Docs", style="Sidebar.TButton", command=lambda: ManualWindow(self.root))
        self.btn_help.pack(fill=X, pady=2)
        hint(self.btn_help, "Open the documentation window with\nstep-by-step guides and troubleshooting.")

        # Content Area
        content_area = ttk.Frame(main_container)
        content_area.pack(side=LEFT, fill=BOTH, expand=True)

        # Top Bar
        self.top_bar = ttk.Frame(content_area, padding=15, bootstyle="bg")
        self.top_bar.pack(fill=X)
        self.lbl_title = ttk.Label(self.top_bar, text="Dashboard", font=("Segoe UI", 18))
        self.lbl_title.pack(side=LEFT)
        self.status_badge = ttk.Label(self.top_bar, text="Ready", bootstyle="success-inverse", padding=(10, 5))
        self.status_badge.pack(side=RIGHT)

        # Pages
        self.page_container = ttk.Frame(content_area)
        self.page_container.pack(fill=BOTH, expand=True, padx=20, pady=(0, 20))

        # Log & Progress
        term_frame = ttk.Labelframe(content_area, text=" Log Output ", padding=0, bootstyle="secondary")
        term_frame.pack(fill=X, padx=20, pady=(0, 20), ipady=5)
        
        self.log_widget = tk.Text(term_frame, height=6, state=tk.DISABLED, bg="#002b36", fg="#2aa198", font=("Consolas", 10), bd=0)
        self.log_widget.pack(side=LEFT, fill=BOTH, expand=True, padx=5, pady=5)
        scr = ttk.Scrollbar(term_frame, command=self.log_widget.yview); scr.pack(side=RIGHT, fill=Y)
        self.log_widget.config(yscrollcommand=scr.set)
        
        self.progress = ttk.Progressbar(content_area, mode='determinate', bootstyle="info-striped")
        self.progress.pack(fill=X, side=BOTTOM)

        self._init_dev_page()
        self._init_fxc_page()
        self._init_awc_page()
        self._init_renodx_page()
        self._show_page("dev")

    def _show_page(self, page_name):
        for page in self.pages.values(): page.pack_forget()
        self.btn_dev.configure(bootstyle="dark")
        self.btn_fxc.configure(bootstyle="dark")
        if hasattr(self, 'btn_awc'):
            self.btn_awc.configure(bootstyle="dark")
        if hasattr(self, 'btn_renodx'):
            self.btn_renodx.configure(bootstyle="dark")

        if page_name in self.pages:
            self.pages[page_name].pack(fill=BOTH, expand=True)
            self.current_page = page_name
        
        if page_name == "dev":
            self.lbl_title.config(text="Compile & Decompile Shaders")
            self.btn_dev.configure(bootstyle="primary") 
        elif page_name == "fxc":
            self.lbl_title.config(text="FXC Archives")
            self.btn_fxc.configure(bootstyle="primary")
        elif page_name == "awc":
            self.lbl_title.config(text="AWC Archives")
            if hasattr(self, 'btn_awc'):
                self.btn_awc.configure(bootstyle="primary")
        elif page_name == "renodx":
            self.lbl_title.config(text="RenoDX Sync")
            if hasattr(self, 'btn_renodx'):
                self.btn_renodx.configure(bootstyle="primary")

    # ------------------------------------------------------------------
    # RenoDX Sync page
    # ------------------------------------------------------------------
    def _init_renodx_page(self):
        page = ttk.Frame(self.page_container)
        self.pages["renodx"] = page

        self.renodx_game_var = tk.StringVar(value=self.config["RenoDX"].get("game_path", ""))
        self.renodx_pretty_var = tk.BooleanVar(value=False)
        self.renodx_update_meta_var = tk.BooleanVar(value=False)
        self.renodx_inplace_var = tk.BooleanVar(value=False)
        self.renodx_dryrun_var = tk.BooleanVar(value=False)
        self.renodx_paths_var = tk.StringVar(value="")

        intro = ttk.Label(
            page,
            text="Bridge RenoDX and the .awc shader library. Set your game folder once, "
                 "then export the effect map into renodx-dev and rebuild the .awc from "
                 "shaders you edited in RenoDX's live folder.",
            wraplength=900, bootstyle="secondary")
        intro.pack(fill=X, pady=(0, 10))

        # --- Game folder ---
        gf = ttk.Labelframe(page, text=" Game Folder (contains GTA5_Enhanced.exe) ", padding=10, bootstyle="info")
        gf.pack(fill=X, pady=(0, 10))
        row = ttk.Frame(gf); row.pack(fill=X)
        ttk.Entry(row, textvariable=self.renodx_game_var).pack(side=LEFT, fill=X, expand=True, padx=(0, 5))
        ttk.Button(row, text="Browse…", command=self._renodx_browse_game, bootstyle="secondary").pack(side=LEFT, padx=2)
        ttk.Button(row, text="Save", command=self._renodx_save_game_path, bootstyle="primary").pack(side=LEFT, padx=2)
        ttk.Label(gf, textvariable=self.renodx_paths_var, bootstyle="secondary", font=("Consolas", 9)).pack(fill=X, pady=(8, 0))

        # --- Effect map export ---
        ef = ttk.Labelframe(page, text=" Effect Map (shader_effects.json) ", padding=10, bootstyle="secondary")
        ef.pack(fill=X, pady=(0, 10))
        ttk.Label(ef, text="Builds the hash→effect map from the .awc and writes it into "
                           "<game>\\renodx-dev so the devkit Shaders tab can group by effect.",
                  wraplength=900, bootstyle="secondary").pack(fill=X, pady=(0, 8))
        erow = ttk.Frame(ef); erow.pack(fill=X)
        ttk.Button(erow, text="▶ Export → renodx-dev", command=self._renodx_export_async, bootstyle="success").pack(side=LEFT)
        ttk.Checkbutton(erow, text="Pretty (readable JSON)", variable=self.renodx_pretty_var, bootstyle="round-toggle").pack(side=LEFT, padx=15)

        # --- Rebuild AWC from live ---
        rf = ttk.Labelframe(page, text=" Rebuild .awc from RenoDX Live Folder ", padding=10, bootstyle="warning")
        rf.pack(fill=X, pady=(0, 10))
        ttk.Label(rf, text="Compiles shaders from <game>\\renodx-dev\\live, matches each to its "
                           ".awc slot by hash, and writes an updated .awc (original backed up). "
                           "Uses the .awc loaded on the AWC tab, or the bundled awc_files if none.",
                  wraplength=900, bootstyle="secondary").pack(fill=X, pady=(0, 8))
        orow = ttk.Frame(rf); orow.pack(fill=X)
        ttk.Checkbutton(orow, text="Update metadata", variable=self.renodx_update_meta_var, bootstyle="round-toggle").pack(side=LEFT, padx=(0, 15))
        ttk.Checkbutton(orow, text="Overwrite in place", variable=self.renodx_inplace_var, bootstyle="round-toggle").pack(side=LEFT, padx=(0, 15))
        ttk.Checkbutton(orow, text="Dry run", variable=self.renodx_dryrun_var, bootstyle="round-toggle").pack(side=LEFT)
        brow = ttk.Frame(rf); brow.pack(fill=X, pady=(8, 0))
        ttk.Button(brow, text="▶ Rebuild .awc from Live", command=self._renodx_rebuild_async, bootstyle="warning").pack(side=LEFT)
        ttk.Button(brow, text="📂 Open Live Folder", command=self._renodx_open_live, bootstyle="secondary").pack(side=LEFT, padx=8)

        # --- Full sync ---
        sf = ttk.Labelframe(page, text=" One-Click Sync ", padding=10, bootstyle="success")
        sf.pack(fill=X, pady=(0, 10))
        ttk.Label(sf, text="Rebuild the .awc from live, then re-export the effect map into "
                           "renodx-dev so the devkit stays in sync after your edits.",
                  wraplength=900, bootstyle="secondary").pack(fill=X, pady=(0, 8))
        ttk.Button(sf, text="⟳ Rebuild + Re-export Effects", command=self._renodx_full_sync_async, bootstyle="success-outline").pack(side=LEFT)

        # --- Legacy (DX11) placeholder ---
        lf = ttk.Labelframe(page, text=" GTA 5 Legacy (DX11) — Coming Soon ", padding=10, bootstyle="secondary")
        lf.pack(fill=X, pady=(0, 10))
        ttk.Label(lf, text="The sections above target GTA 5 Enhanced (DX12 / .awc). A Legacy/DX11 "
                           "version is planned. GTA 5 Legacy stores shaders in .fxc archives where "
                           "each archive is already an effect group (rather than one big library with "
                           "an effect table), so the effect-map and rebuild flow will be adapted to "
                           "that layout. Not available yet.",
                  wraplength=900, bootstyle="secondary").pack(fill=X, pady=(0, 8))
        ttk.Button(lf, text="GTA 5 Legacy Sync (Coming Soon)", bootstyle="secondary", state="disabled").pack(side=LEFT)

        self._renodx_update_paths_label()

    def _renodx_browse_game(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(title="Select GTA 5 Enhanced game folder")
        if d:
            self.renodx_game_var.set(d)
            self._renodx_save_game_path()

    def _renodx_save_game_path(self):
        self.config["RenoDX"]["game_path"] = self.renodx_game_var.get().strip()
        self.cfg_mgr.save()
        self._renodx_update_paths_label()
        self._log(f"RenoDX game folder saved: {self.renodx_game_var.get().strip() or '(none)'}")

    def _renodx_paths(self):
        """Return (game, renodx_dev, live, sidecar_dest) Path objects, or None."""
        game = self.renodx_game_var.get().strip()
        if not game:
            return None
        game = os.path.abspath(game)
        renodx_dev = os.path.join(game, "renodx-dev")
        return (game, renodx_dev,
                os.path.join(renodx_dev, "live"),
                os.path.join(renodx_dev, "shader_effects.json"))

    def _renodx_update_paths_label(self):
        p = self._renodx_paths()
        if not p:
            self.renodx_paths_var.set("No game folder set.")
            return
        game, renodx_dev, live, sidecar = p
        exe = os.path.join(game, "GTA5_Enhanced.exe")
        ok = "✓" if os.path.exists(exe) else "✗ (GTA5_Enhanced.exe not found here)"
        self.renodx_paths_var.set(
            f"{ok}\nlive:    {live}\nsidecar: {sidecar}")

    def _renodx_target_awcs(self):
        """AWC(s) to operate on.

        GTA5 Enhanced ships the shader library as a PAIR: the big archive
        (e.g. sga_win32_60_final.awc) and its small companion that ends in
        '_init.awc'. A given shader lives in exactly one of the two, so we
        always operate on BOTH so the right file is matched/updated.

        If an .awc is loaded on the AWC tab, use it plus its sibling (found by
        adding/removing the '_init' suffix in the same folder). Otherwise fall
        back to the bundled awc_files pair.
        """
        loaded = getattr(self, "awc_current_filepath", None)
        if loaded and os.path.exists(loaded):
            loaded = os.path.abspath(loaded)
            folder = os.path.dirname(loaded)
            stem, ext = os.path.splitext(os.path.basename(loaded))
            if stem.endswith("_init"):
                sibling_stem = stem[:-len("_init")]
            else:
                sibling_stem = stem + "_init"
            sibling = os.path.join(folder, sibling_stem + ext)
            paths = [loaded]
            if os.path.exists(sibling):
                paths.append(sibling)
                return paths, f"loaded AWC + sibling ({os.path.basename(loaded)} + {os.path.basename(sibling)})"
            return paths, f"loaded AWC ({os.path.basename(loaded)}) — no '_init' sibling found next to it"
        from tools.rebuild_awc_from_live import _DEFAULT_AWCS
        return [str(p) for p in _DEFAULT_AWCS], "bundled awc_files (both)"

    def _renodx_open_live(self):
        p = self._renodx_paths()
        if not p:
            self._log("Set the game folder first.")
            return
        live = p[2]
        os.makedirs(live, exist_ok=True)
        subprocess.Popen(f'explorer "{os.path.abspath(live)}"')

    def _renodx_export_async(self):
        p = self._renodx_paths()
        if not p:
            self._log("Set the game folder first.")
            return
        sidecar_dest = p[3]
        awcs, awc_desc = self._renodx_target_awcs()
        pretty = self.renodx_pretty_var.get()
        self.msg_queue.put(("P_START", 1))
        threading.Thread(target=self._renodx_export_worker,
                         args=(awcs, awc_desc, sidecar_dest, pretty), daemon=True).start()

    def _renodx_export_worker(self, awcs, awc_desc, sidecar_dest, pretty):
        try:
            from tools import export_shader_effects as E
            self._log(f"Exporting effect map from {awc_desc} ...")
            os.makedirs(os.path.dirname(sidecar_dest), exist_ok=True)
            stats = E.export_sidecar(awcs, sidecar_dest, pretty=pretty, log=self._log)
            self._log(f"Done: {stats['shaders']} shaders / {stats['effects']} effects -> {sidecar_dest}")
        except Exception as e:
            self._log(f"Export FAILED: {e}")
        finally:
            self.msg_queue.put(("P_STOP", None))

    def _renodx_rebuild_async(self):
        p = self._renodx_paths()
        if not p:
            self._log("Set the game folder first.")
            return
        live = p[2]
        if not os.path.isdir(live):
            self._log(f"Live folder not found: {live}")
            return
        awcs, awc_desc = self._renodx_target_awcs()
        opts = dict(update_metadata=self.renodx_update_meta_var.get(),
                    in_place=self.renodx_inplace_var.get(),
                    dry_run=self.renodx_dryrun_var.get())
        self.msg_queue.put(("P_START", 1))
        threading.Thread(target=self._renodx_rebuild_worker,
                         args=(live, awcs, awc_desc, opts, False), daemon=True).start()

    def _renodx_rebuild_worker(self, live, awcs, awc_desc, opts, then_export):
        try:
            from tools import rebuild_awc_from_live as R
            self._log(f"Rebuilding {awc_desc} from live folder ...")
            res = R.run_rebuild(
                live=live, awc=awcs, out_dir=self.dirs["awc"],
                in_place=opts["in_place"], update_metadata=opts["update_metadata"],
                dxc_path=self.tools["dxc"], log=self._log, dry_run=opts["dry_run"])
            if res.get("error"):
                self._log(f"Rebuild FAILED: {res['error']}")
                return
            self._log(f"Rebuild done: {res['changes']} slot(s) updated across "
                      f"{len(res['written'])} file(s).")
            if then_export and res["written"] and not opts["dry_run"]:
                p = self._renodx_paths()
                if p:
                    from tools import export_shader_effects as E
                    self._log("Re-exporting effect map ...")
                    # Re-export from the COMPLETE set so no file's shaders are
                    # dropped: use each target's modified copy if it was written,
                    # otherwise the original. (In-place writes overwrite the
                    # originals, so the target list is already current.)
                    if opts["in_place"]:
                        out_awcs = awcs
                    else:
                        written = {os.path.abspath(w) for w in res["written"]}
                        out_dir = self.dirs["awc"]
                        out_awcs = []
                        for a in awcs:
                            stem = os.path.splitext(os.path.basename(a))[0]
                            mod = os.path.abspath(os.path.join(out_dir, stem + "_modified.awc"))
                            out_awcs.append(mod if mod in written else a)
                    stats = E.export_sidecar(out_awcs, p[3],
                                             pretty=self.renodx_pretty_var.get(), log=self._log)
                    self._log(f"Effect map updated: {stats['shaders']} shaders -> {p[3]}")
            self.config["RenoDX"]["last_sync"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
            self.cfg_mgr.save()
        except Exception as e:
            self._log(f"Rebuild FAILED: {e}")
        finally:
            self.msg_queue.put(("P_STOP", None))

    def _renodx_full_sync_async(self):
        p = self._renodx_paths()
        if not p:
            self._log("Set the game folder first.")
            return
        live = p[2]
        if not os.path.isdir(live):
            self._log(f"Live folder not found: {live}")
            return
        awcs, awc_desc = self._renodx_target_awcs()
        opts = dict(update_metadata=self.renodx_update_meta_var.get(),
                    in_place=self.renodx_inplace_var.get(),
                    dry_run=self.renodx_dryrun_var.get())
        self.msg_queue.put(("P_START", 1))
        threading.Thread(target=self._renodx_rebuild_worker,
                         args=(live, awcs, awc_desc, opts, True), daemon=True).start()

    def _init_dev_page(self):
        page = ttk.Frame(self.page_container)
        self.pages["dev"] = page
        
        # --- Welcome Banner (dismissible) ---
        if self.show_welcome:
            self.welcome_frame = ttk.Frame(page, padding=10, bootstyle="info")
            self.welcome_frame.pack(fill=X, pady=(0, 10))
            
            ttk.Label(self.welcome_frame, text="📌  Getting Started", font=("Segoe UI", 10, "bold"), bootstyle="inverse-info").pack(side=LEFT, padx=(0, 10))
            ttk.Label(self.welcome_frame, text="👋 New here? To mod a GTA V shader, you first need to unpack the game's shader archive. For GTA 5 Legacy (DX11) use 'FXC Archives', for GTA 5 Enhanced (DX12) use 'AWC Archives' — then decompile it to get editable code, make your changes, compile, and repack. Click 'Help & Docs' on the left for a full step-by-step guide.", wraplength=900, bootstyle="inverse-info").pack(side=LEFT, fill=X, expand=True)
            
            def dismiss_banner():
                self.welcome_frame.pack_forget()
                self.show_welcome = False
                self.config["Window"]["show_welcome_banner"] = "false"
            
            ttk.Button(self.welcome_frame, text="✕ Dismiss", command=dismiss_banner, bootstyle="outline-light").pack(side=RIGHT, padx=(10, 0))
        
        # --- Toolbar ---
        toolbar = ttk.Frame(page, padding=(0, 0, 0, 10))
        toolbar.pack(fill=X)

        # DirectX Version section
        ttk.Label(toolbar, text="DirectX Version:", font=("Segoe UI", 9, "bold"), foreground="#d33682").pack(side=LEFT, padx=(0, 5))
        rb_dx11 = ttk.Radiobutton(toolbar, text="DX11 — Legacy", variable=self.dx_version, value="dx11", command=self._on_dx_change, bootstyle="toolbutton-primary")
        rb_dx11.pack(side=LEFT, padx=2)
        hint(rb_dx11, "GTA 5 Legacy (older version).\nDirectX 11, Shader Model 5.0, fxc.exe compiler.\nUses .fxc shader archives.")
        rb_dx12 = ttk.Radiobutton(toolbar, text="DX12 — Enhanced", variable=self.dx_version, value="dx12", command=self._on_dx_change, bootstyle="toolbutton-danger")
        rb_dx12.pack(side=LEFT, padx=2)
        hint(rb_dx12, "GTA 5 Enhanced (new version).\nDirectX 12, Shader Model 6.0+, dxc.exe compiler.\nUses .awc shader archives.")
        rb_rdr1 = ttk.Radiobutton(toolbar, text="RDR1 — DX12", variable=self.dx_version, value="rdr1", command=self._on_dx_change, bootstyle="toolbutton-success")
        rb_rdr1.pack(side=LEFT, padx=2)
        hint(rb_rdr1, "Red Dead Redemption (PC port).\nDirectX 12, Shader Model 6.0, dxc.exe compiler.\nUses rgxd .fxc shader archives (RDR1 FXC tab).\nI/O semantics auto-restored on decompile.")
        
        ttk.Separator(toolbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=10)

        # Decompilation Method (DX12 only - shown/hidden by _on_dx_change)
        self.fr_decomp_method = ttk.Frame(toolbar)
        ttk.Label(self.fr_decomp_method, text="Decompile Method:", font=("Segoe UI", 9, "bold"), foreground="#cb4b16").pack(side=LEFT, padx=(0, 5))
        rb_spirv = ttk.Radiobutton(self.fr_decomp_method, text="SPIR-V", variable=self.decomp_method_var, value="spirv", bootstyle="toolbutton-warning")
        rb_spirv.pack(side=LEFT, padx=2)
        hint(rb_spirv, "Two-step pipeline: dxil-spirv → spirv-cross.\nProduces cleaner HLSL but may miss some features.")
        rb_decomp = ttk.Radiobutton(self.fr_decomp_method, text="Decomp.exe", variable=self.decomp_method_var, value="decomp", bootstyle="toolbutton-secondary")
        rb_decomp.pack(side=LEFT, padx=2)
        hint(rb_decomp, "Direct decompilation using decomp.exe.\nFallback method for shaders that fail SPIR-V pipeline.")
        
        self.sep_view_mode = ttk.Separator(toolbar, orient=VERTICAL)
        self.sep_view_mode.pack(side=LEFT, fill=Y, padx=10)

        # View Mode section
        ttk.Label(toolbar, text="File View:", font=("Segoe UI", 9, "bold"), foreground="#93a1a1").pack(side=LEFT, padx=(0, 5))
        rb_workspace = ttk.Radiobutton(toolbar, text="📂 Workspace", variable=self.source_mode_var, value="source", command=self.refresh_source, bootstyle="toolbutton-info")
        rb_workspace.pack(side=LEFT, padx=2)
        hint(rb_workspace, "Show files from the /source/ folder.\nThis is your main editing workspace.")
        rb_decompiled = ttk.Radiobutton(toolbar, text="🔓 Decompiled", variable=self.source_mode_var, value="decompiled", command=self.refresh_source, bootstyle="toolbutton-secondary")
        rb_decompiled.pack(side=LEFT, padx=2)
        hint(rb_decompiled, "Show files from the /decompiled/ folder.\nRead-only reference of decompiled shaders.")

        btn_refresh = ttk.Button(toolbar, text="⟳ Refresh All", command=self.refresh_all, bootstyle="light-outline")
        btn_refresh.pack(side=LEFT, padx=15)
        hint(btn_refresh, "Reload all file lists across every tab.")

        chk_watch = ttk.Checkbutton(toolbar, text="Auto-Compile (Watch)", variable=self.watcher_var, command=self._toggle_watcher, bootstyle="round-toggle-success")
        chk_watch.pack(side=RIGHT)
        hint(chk_watch, "When enabled, saving a .hlsl file in your\nexternal editor triggers an instant recompile.")

        # --- Paned panels ---
        panes = ttk.PanedWindow(page, orient=HORIZONTAL)
        panes.pack(fill=BOTH, expand=True)

        # LEFT PANEL - Source Files
        p1 = ttk.Frame(panes)
        panes.add(p1, weight=1)
        
        ttk.Label(p1, text="📂  Source Files (.hlsl)", font=("Segoe UI", 10, "bold"), foreground="#268bd2").pack(fill=X, pady=(0, 5))
        
        search_fr = ttk.Frame(p1, padding=(0, 0, 0, 0))
        search_fr.pack(fill=X)
        search_entry = ttk.Entry(search_fr, textvariable=self.search_var, bootstyle="primary", width=30)
        search_entry.pack(side=LEFT, fill=X, expand=True)
        hint(search_entry, "Type to filter shaders by filename.")
        ttk.Label(search_fr, text="🔍", font=("Segoe UI", 12)).pack(side=RIGHT, padx=5)

        src_container, self.src_tree = self._create_scrolled_tree(p1, show="tree", bootstyle="info")
        src_container.pack(fill=BOTH, expand=True, pady=5)
        
        self.src_tree.bind("<Double-1>", self._open_src_file)
        self.src_tree.bind("<Button-3>", lambda e: self._ctx_menu(e, self.src_tree, "src"))

        act_fr = ttk.Frame(p1, padding=(0, 5, 0, 0))
        act_fr.pack(fill=X)
        
        btn_row = ttk.Frame(act_fr)
        btn_row.pack(fill=X, pady=2)
        btn_sel_src = ttk.Button(btn_row, text="Select All", command=lambda: self._select_all(self.src_tree), bootstyle="secondary-outline")
        btn_sel_src.pack(side=LEFT, fill=X, expand=True, padx=(0, 2))
        hint(btn_sel_src, "Select every source file in the list.")
        btn_compile = ttk.Button(btn_row, text="▶ Compile Selected", command=self.compile_async, bootstyle="success")
        btn_compile.pack(side=LEFT, fill=X, expand=True, padx=(2, 0))
        hint(btn_compile, "Compile the selected .hlsl file(s) to .cso binaries.\nOutput goes to the /compiled/ folder.")
        
        sub_act = ttk.Frame(act_fr); sub_act.pack(fill=X)
        btn_defines = ttk.Button(sub_act, text="⚙ Global Defines", command=self.open_defines, bootstyle="secondary")
        btn_defines.pack(side=LEFT, fill=X, expand=True, padx=(0, 2))
        hint(btn_defines, "Open the Global Defines manager.\nBulk-edit #define values across multiple shader files.")
        btn_organize = ttk.Button(sub_act, text="📂 Organize Files", command=self.organize_source_files, bootstyle="info")
        btn_organize.pack(side=LEFT, fill=X, expand=True, padx=2)
        hint(btn_organize, "Auto-sort source files into subfolders\nbased on shader type (VS, PS, CS, etc.).")
        btn_edit = ttk.Button(sub_act, text="📝 Open in Editor", command=self._open_editor, bootstyle="secondary")
        btn_edit.pack(side=LEFT, fill=X, expand=True, padx=(2, 0))
        hint(btn_edit, "Open the selected file in your configured\ntext editor (or system default).")

        # RIGHT PANEL - Compiled Output
        p2 = ttk.Frame(panes)
        panes.add(p2, weight=1)
        
        ttk.Label(p2, text="⚙  Compiled Output (.cso)", font=("Segoe UI", 10, "bold"), foreground="#b58900").pack(fill=X, pady=(0, 5))
        
        cmp_container, self.cmp_tree = self._create_scrolled_tree(p2, show="tree", bootstyle="warning")
        cmp_container.pack(fill=BOTH, expand=True)
        
        self.cmp_tree.bind("<Button-3>", lambda e: self._ctx_menu(e, self.cmp_tree, "cmp"))
        
        # Right panel buttons
        r_btns = ttk.Frame(p2)
        r_btns.pack(fill=X, pady=(10, 0))
        
        btn_sel_cmp = ttk.Button(r_btns, text="Select All", command=lambda: self._select_all(self.cmp_tree), bootstyle="secondary-outline")
        btn_sel_cmp.pack(side=LEFT, fill=X, expand=True, padx=(0, 2))
        hint(btn_sel_cmp, "Select every compiled binary in the list.")
        btn_asm = ttk.Button(r_btns, text="📜 View ASM", command=self.view_asm_selected, bootstyle="info-outline")
        btn_asm.pack(side=LEFT, fill=X, expand=True, padx=2)
        hint(btn_asm, "Disassemble the selected .cso and view\nthe raw assembly instructions.")
        btn_decompile = ttk.Button(r_btns, text="▼ Decompile to HLSL", command=self.decompile_async, bootstyle="warning")
        btn_decompile.pack(side=LEFT, fill=X, expand=True, padx=(2, 0))
        hint(btn_decompile, "Decompile selected .cso file(s) back to\n.hlsl source code in the /decompiled/ folder.")

        # Context Menus
        self.ctx_src = tk.Menu(self.root, tearoff=0, bg="#073642", fg="#93a1a1", activebackground="#2aa198", activeforeground="white")
        self.ctx_src.add_command(label="Open File Location", command=self._ctx_open_folder)
        self.ctx_src.add_command(label="Open in Editor", command=self._open_editor)

        self.ctx_cmp = tk.Menu(self.root, tearoff=0, bg="#073642", fg="#93a1a1", activebackground="#2aa198", activeforeground="white")
        self.ctx_cmp.add_command(label="View ASM Code", command=self.view_asm_selected)
        self.ctx_cmp.add_command(label="Open File Location", command=self._ctx_open_folder)

    def _init_fxc_page(self):
        page = ttk.Frame(self.page_container)
        self.pages["fxc"] = page
        
        # --- Info Banner ---
        banner = ttk.Frame(page, padding=10, bootstyle="warning")
        banner.pack(fill=X, pady=(0, 10))
        self.fxc_banner_title = ttk.Label(banner, text="📦  GTA 5 Legacy — DX11 Archives", font=("Segoe UI", 10, "bold"), bootstyle="inverse-warning")
        self.fxc_banner_title.pack(side=LEFT, padx=(0, 10))
        self.fxc_banner_body = ttk.Label(banner, text="This tab is for GTA 5 Legacy (older version). Select a .fxc archive file on the left, then: 1) Unpack it to extract shaders,  2) Go to 'Compile & Decompile' to edit and recompile,  3) Come back here and Repack to inject your changes back into the archive.", wraplength=900, bootstyle="inverse-warning")
        self.fxc_banner_body.pack(side=LEFT, fill=X, expand=True)
        

        tools = ttk.Frame(page)
        tools.pack(fill=X, pady=(0, 10))
        
        btn_fxc_sel = ttk.Button(tools, text="Select All", command=lambda: self._select_all(self.fxc_tree), bootstyle="secondary-outline")
        btn_fxc_sel.pack(side=LEFT, padx=(0, 5))
        hint(btn_fxc_sel, "Select all FXC archives in the workspace list.")
        btn_unpack = ttk.Button(tools, text="📦 Unpack", command=self.unpack_fxc, bootstyle="info")
        btn_unpack.pack(side=LEFT, padx=(0, 5))
        hint(btn_unpack, "Extract all shaders from the selected .fxc\narchive(s) into /compiled/dx11/ as .cso files.")
        btn_repack = ttk.Button(tools, text="📤 Repack", command=self.repack_fxc, bootstyle="warning")
        btn_repack.pack(side=LEFT, padx=(0, 15))
        hint(btn_repack, "Inject modified .cso files back into the\nselected .fxc archive. Only changed shaders\nare updated. A backup is created automatically.")

        btn_fxc_save = ttk.Button(tools, text="💾 Save FXC", command=self._fxc_save, bootstyle="success")
        btn_fxc_save.pack(side=LEFT, padx=(0, 15))
        hint(btn_fxc_save, "Save the currently loaded FXC to a file.\nUse after importing CSO data into shaders.")
        
        btn_fxc_imp = ttk.Button(tools, text="📥 Import CSO", command=self._fxc_import, bootstyle="warning")
        btn_fxc_imp.pack(side=LEFT, padx=(0, 5))
        hint(btn_fxc_imp, "Replace the selected shader's bytecode with\na .cso file from disk. Remember to Save FXC after.")
        btn_fxc_exp = ttk.Button(tools, text="📤 Export CSO", command=self._fxc_export, bootstyle="secondary")
        btn_fxc_exp.pack(side=LEFT)
        hint(btn_fxc_exp, "Export the selected shader(s) as .cso files\nto /compiled/dx11/<archive_name>/<type>/.")
        
        btn_fxc_ref = ttk.Button(tools, text="⟳ Refresh", command=self.refresh_all, bootstyle="link")
        btn_fxc_ref.pack(side=RIGHT)
        hint(btn_fxc_ref, "Reload all file lists.")

        self.fxc_status_var = tk.StringVar(value="Ready — No FXC selected")
        ttk.Label(tools, textvariable=self.fxc_status_var, bootstyle="secondary-inverse", padding=(5, 2)).pack(side=RIGHT, padx=10)

        panes = ttk.PanedWindow(page, orient=HORIZONTAL)
        panes.pack(fill=BOTH, expand=True)

        # LEFT: Workspace FXC Archives
        files_fr = ttk.Labelframe(panes, text=" FXC Archives in /fxc_files/ ", padding=5)
        panes.add(files_fr, weight=1)

        search_fr = ttk.Frame(files_fr)
        search_fr.pack(fill=X, pady=(0, 5))
        ttk.Label(search_fr, text="🔍 Filter:", font=("Segoe UI", 9)).pack(side=LEFT, padx=(0, 5))
        self.fxc_search_var = tk.StringVar()
        self.fxc_search_var.trace_add("write", lambda *args: self.refresh_fxc_list())
        ttk.Entry(search_fr, textvariable=self.fxc_search_var).pack(side=LEFT, fill=X, expand=True)

        cols = ("size", "count", "path")
        fxc_container, self.fxc_tree = self._create_scrolled_tree(files_fr, columns=cols, show="headings", bootstyle="primary")
        fxc_container.pack(fill=BOTH, expand=True)

        self.fxc_tree.heading("size", text="Size"); self.fxc_tree.column("size", width=80, anchor="e")
        self.fxc_tree.heading("count", text="Shaders"); self.fxc_tree.column("count", width=80, anchor="e")
        self.fxc_tree.heading("path", text="Filename"); self.fxc_tree.column("path", width=200)
        self.fxc_tree.bind("<<TreeviewSelect>>", self._on_fxc_file_select)
        self.fxc_tree.bind("<Double-1>", self._on_fxc_double_click)

        # MIDDLE: Shaders inside FXC
        middle_fr = ttk.Frame(panes)
        panes.add(middle_fr, weight=2)
        
        sh_search_fr = ttk.Frame(middle_fr)
        sh_search_fr.pack(fill=X, pady=(0, 5))
        ttk.Label(sh_search_fr, text="🔍 Filter shaders:", font=("Segoe UI", 9)).pack(side=LEFT, padx=(0, 5))
        self.fxc_shader_search_var = tk.StringVar()
        self.fxc_shader_search_var.trace_add("write", lambda *args: self._fxc_populate_tree())
        ttk.Entry(sh_search_fr, textvariable=self.fxc_shader_search_var).pack(side=LEFT, fill=X, expand=True)

        cols_s = ("renohash", "type", "size")
        tree_container, self.fxc_shader_tree = self._create_scrolled_tree(middle_fr, columns=cols_s, show="tree headings", bootstyle="info")
        tree_container.pack(fill=BOTH, expand=True)

        self.fxc_shader_tree.heading("#0", text="Shader Name", anchor="w")
        self.fxc_shader_tree.heading("renohash", text="RenoDX Hash", anchor="w")
        self.fxc_shader_tree.heading("type", text="Type", anchor="w")
        self.fxc_shader_tree.heading("size", text="Size (Bytes)", anchor="e")
        self.fxc_shader_tree.column("#0", width=250)
        self.fxc_shader_tree.column("renohash", width=95, anchor="w")
        self.fxc_shader_tree.column("type", width=80)
        self.fxc_shader_tree.column("size", width=100, anchor="e")
        self.fxc_shader_tree.bind("<<TreeviewSelect>>", self._on_fxc_shader_select)
        
        # RIGHT: Details
        details_fr = ttk.Labelframe(panes, text=" Shader Details ", padding=10)
        panes.add(details_fr, weight=1)
        
        self.fxc_details = tk.Text(details_fr, font=("Consolas", 10), bg="#002b36", fg="#93a1a1", bd=0, wrap="word")
        self.fxc_details.pack(fill=BOTH, expand=True)
        self.fxc_details.config(state=tk.NORMAL)
        self.fxc_details.insert(tk.END, "Select a shader from the list\nto view its details here.")
        self.fxc_details.config(state=tk.DISABLED)
        
        # State variables
        self.fxc_current_file = None
        self.fxc_current_shader = None

    def _on_fxc_double_click(self, event):
        item = self.fxc_tree.identify_row(event.y)
        if item:
            self.fxc_tree.selection_set(item)
            self.unpack_fxc()

    def _on_fxc_file_select(self, event):
        sel = self.fxc_tree.selection()
        if not sel: return
        tags = self.fxc_tree.item(sel[0], "tags")
        if tags and len(tags) > 0:
            filepath = tags[0]
            if os.path.exists(filepath):
                self._load_fxc_file(filepath)

    def _load_fxc_file(self, filepath):
        # RDR1 rgxd container: parse with rdr1_fxc, not the DX11 FxcFile.
        if self.dx_version.get() == "rdr1":
            try:
                with open(filepath, 'rb') as f: data = f.read()
                self.fxc_is_rdr1 = True
                self.fxc_current_file = None
                self._fxc_rdr1_data = data
                self.fxc_current_filepath = filepath
                self._fxc_populate_tree()
                self.fxc_status_var.set(f"Loaded (RDR1): {os.path.basename(filepath)}")
            except Exception as e:
                self.fxc_status_var.set("Error parsing RDR1 FXC")
                self._log(f"RDR1 FXC Parse Error: {e}")
            return
        try:
            self._log(f"Parsing FXC: {os.path.basename(filepath)}...")
            with open(filepath, 'rb') as f: data = f.read()
            self.fxc_is_rdr1 = False
            self.fxc_current_file = fxc_parser.FxcFile()
            self.fxc_current_file.load(data)

            self.fxc_current_filepath = filepath

            self.fxc_status_var.set(f"Loaded: {os.path.basename(filepath)}")
            self._fxc_populate_tree()
            self._log(f"Successfully loaded FXC headers.")
        except Exception as e:
            self.fxc_status_var.set(f"Error parsing FXC")
            self._log(f"FXC Parse Error: {e}")

    def _fxc_populate_rdr1(self):
        """Populate the middle shader tree from an RDR1 rgxd container,
        grouped by stage inferred from the shader-name prefix."""
        self.fxc_shader_tree.delete(*self.fxc_shader_tree.get_children())
        data = getattr(self, "_fxc_rdr1_data", None)
        if not data: return
        search_term = self.fxc_shader_search_var.get().lower() if hasattr(self, 'fxc_shader_search_var') else ""
        blobs = rdr1_fxc.scan(data)

        def stage_of(name):
            for s in ("VS", "PS", "CS", "GS", "HS", "DS"):
                if name.upper().startswith(s): return s
            return "Misc"

        groups = {}
        self.fxc_rdr1_map = {}
        for b in blobs:
            if search_term and search_term not in b.name.lower(): continue
            st = stage_of(b.name)
            if st not in groups:
                groups[st] = self.fxc_shader_tree.insert("", "end", text=f"{st} Shaders", values=("", "", ""), open=True)
            blob_bytes = data[b.offset:b.offset + b.size]
            item = self.fxc_shader_tree.insert(groups[st], "end", text=f"{b.index:02d}_{b.name}",
                                               values=(renodx_hash(blob_bytes), st, f"{b.size:,}"))
            self.fxc_rdr1_map[item] = b

    def _fxc_populate_tree(self):
        if getattr(self, "fxc_is_rdr1", False):
            self._fxc_populate_rdr1()
            return
        self.fxc_shader_tree.delete(*self.fxc_shader_tree.get_children())
        if not self.fxc_current_file: return
        
        search_term = ""
        if hasattr(self, 'fxc_shader_search_var'):
            search_term = self.fxc_shader_search_var.get().lower()
            
        categories = [
            ("Vertex Shaders", self.fxc_current_file.ShaderGroups[0].Shaders, "VS"),
            ("Pixel Shaders", self.fxc_current_file.ShaderGroups[1].Shaders, "PS"),
            ("Compute Shaders", self.fxc_current_file.ShaderGroups[2].Shaders, "CS"),
            ("Domain Shaders", self.fxc_current_file.ShaderGroups[3].Shaders, "DS"),
            ("Geometry Shaders", self.fxc_current_file.ShaderGroups[4].Shaders, "GS"),
            ("Hull Shaders", self.fxc_current_file.ShaderGroups[5].Shaders, "HS")
        ]
        
        self.fxc_shader_map = {}
        for cat_name, shaders, sh_type in categories:
            if not shaders: continue
            
            filtered = []
            for s in shaders:
                if search_term in s.Name.lower():
                    filtered.append(s)
                    
            if not filtered: continue
            
            cat_id = self.fxc_shader_tree.insert("", "end", text=f"{cat_name} ({len(filtered)})", values=("", "", ""), open=True)
            for s in filtered:
                item_id = self.fxc_shader_tree.insert(cat_id, "end", text=s.Name, values=(renodx_hash(s.ByteCode), sh_type, f"{len(s.ByteCode):,}"))
                self.fxc_shader_map[item_id] = s

    def _on_fxc_shader_select(self, event):
        sel = self.fxc_shader_tree.selection()
        # RDR1: minimal details from the scanned blob (no DX11 reflection tables).
        if getattr(self, "fxc_is_rdr1", False):
            if sel and hasattr(self, 'fxc_rdr1_map') and sel[0] in self.fxc_rdr1_map:
                b = self.fxc_rdr1_map[sel[0]]
                blob = self._fxc_rdr1_data[b.offset:b.offset + b.size]
                info = (f"Name: {b.name}\nIndex: {b.index}\nRenoDX Hash: {renodx_hash(blob)}\n"
                        f"Size: {b.size:,} bytes\nOffset: 0x{b.offset:X}\n\n"
                        f"RDR1 SM6.0 DXIL. Use Unpack to extract, then the "
                        f"'Compile & Decompile' tab (RDR1 profile) to edit.")
                if hasattr(self, 'fxc_details'):
                    self.fxc_details.config(state=tk.NORMAL)
                    self.fxc_details.delete("1.0", tk.END)
                    self.fxc_details.insert(tk.END, info)
                    self.fxc_details.config(state=tk.DISABLED)
            return
        if sel and hasattr(self, 'fxc_shader_map') and sel[0] in self.fxc_shader_map:
            shader = self.fxc_shader_map[sel[0]]
            self.fxc_current_shader = shader
            
            details = f"Name: {shader.Name}\n"
            details += f"RenoDX Hash: {renodx_hash(shader.ByteCode)}\n"
            details += f"Binary Size: {len(shader.ByteCode):,} bytes\n"
            version_str = f"{shader.VersionMajor}.{shader.VersionMinor}"
            details += f"DXBC Version: {version_str}\n\n"
            
            if shader.Variables:
                details += f"Variables ({len(shader.Variables)}):\n"
                for v in shader.Variables:
                    details += f"  - {v}\n"
                details += "\n"
                
            if shader.Buffers:
                details += f"Buffers ({len(shader.Buffers)}):\n"
                for b in shader.Buffers:
                    details += f"  - [{b.Slot}] {b.Name}\n"
            
            self.fxc_details.config(state=tk.NORMAL)
            self.fxc_details.delete("1.0", tk.END)
            self.fxc_details.insert(tk.END, details)
            self.fxc_details.config(state=tk.DISABLED)
        else:
            self.fxc_current_shader = None
            self.fxc_details.config(state=tk.NORMAL)
            self.fxc_details.delete("1.0", tk.END)
            self.fxc_details.config(state=tk.DISABLED)

    def _fxc_import(self):
        if getattr(self, "fxc_is_rdr1", False):
            return messagebox.showinfo("RDR1", "For RDR1 archives, use Unpack then Repack — "
                                       "the whole archive is handled at once, no per-shader CSO import needed.")
        if not hasattr(self, 'fxc_current_file') or not self.fxc_current_file: return messagebox.showwarning("Warning", "Open an FXC file first.")
        if not hasattr(self, 'fxc_current_shader') or not self.fxc_current_shader: return messagebox.showwarning("Warning", "Select a shader from the list first.")
        
        shader = self.fxc_current_shader
        
        # Determine the group folder ("VS", "PS", etc.)
        g_names = ["VS", "PS", "CS", "DS", "GS", "HS"]
        group_name = "VS"
        for i, grp in enumerate(self.fxc_current_file.ShaderGroups):
            if shader in grp.Shaders:
                group_name = g_names[i]
                break
                
        fxc_name = os.path.splitext(os.path.basename(self.fxc_current_filepath))[0]
        initial_dir = os.path.join(self.dirs["compiled"], "dx11", fxc_name, group_name)
        if not os.path.exists(initial_dir):
            initial_dir = os.path.join(self.dirs["compiled"], "dx11")
            
        filepath = filedialog.askopenfilename(title=f"Import CSO for {shader.Name}", initialdir=initial_dir, filetypes=[("Compiled Shaders", "*.cso"), ("All Files", "*.*")])
        if not filepath: return
        
        try:
            with open(filepath, "rb") as f:
                new_bytecode = f.read()
                
            if len(new_bytecode) == 0:
                messagebox.showerror("Error", "Selected file is empty.")
                return
                
            shader.ByteCode = new_bytecode
            self._log(f"FXC: Imported new bytecode for {shader.Name} ({len(new_bytecode)} bytes)")
            
            # Refresh the tree item size
            sel = self.fxc_shader_tree.selection()
            if sel:
                vals = self.fxc_shader_tree.item(sel[0], "values")
                if vals:
                    self.fxc_shader_tree.item(sel[0], values=(renodx_hash(shader.ByteCode), vals[1], f"{len(shader.ByteCode):,}"))
            
            # Refresh the details panel
            self._on_fxc_shader_select(None)
            
            messagebox.showinfo("Success", f"Successfully imported CSO data to {shader.Name}.\nDon't forget to Save FXC.")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            messagebox.showerror("Error", f"Failed to import CSO:\n{e}")

    def _fxc_export(self):
        if getattr(self, "fxc_is_rdr1", False):
            return messagebox.showinfo("RDR1", "For RDR1 archives, use Unpack — it extracts every "
                                       "shader as .cso into compiled/rdr1/<archive>/ automatically.")
        if not hasattr(self, 'fxc_current_file') or not self.fxc_current_file: return messagebox.showwarning("Warning", "Open an FXC file first.")
        
        sel = self.fxc_shader_tree.selection()
        if not sel: return messagebox.showwarning("Warning", "Select one or more shaders from the list first.")
        
        exported_count = 0
        g_names = ["VS", "PS", "CS", "DS", "GS", "HS"]
        fxc_name = os.path.splitext(os.path.basename(self.fxc_current_filepath))[0]
        base_target_dir = os.path.join(self.dirs["compiled"], "dx11", fxc_name)
        
        try:
            for item in sel:
                if item not in self.fxc_shader_map:
                    continue # Might be a category node
                    
                shader = self.fxc_shader_map[item]
                safe_name = shader.Name.replace("\\", "_").replace("/", "_").replace(":", "_")[:80]
                if not safe_name: safe_name = "shader"
                
                # Determine the group folder ("VS", "PS", etc.)
                group_name = "VS"
                for i, grp in enumerate(self.fxc_current_file.ShaderGroups):
                    if shader in grp.Shaders:
                        group_name = g_names[i]
                        break
                        
                target_dir = os.path.join(base_target_dir, group_name)
                os.makedirs(target_dir, exist_ok=True)
                
                filepath = os.path.join(target_dir, f"{safe_name}.cso")
                
                with open(filepath, "wb") as f:
                    f.write(shader.ByteCode)
                
                self._log(f"FXC: Exported {shader.Name} to {filepath}.")
                exported_count += 1
                
            if exported_count > 0:
                messagebox.showinfo("Success", f"Exported {exported_count} shader(s) to:\n{base_target_dir}")
            else:
                messagebox.showwarning("Warning", "No shaders were exported. Did you select a category instead of a shader?")
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export CSO:\n{e}")

    def _fxc_save(self):
        if getattr(self, "fxc_is_rdr1", False):
            return messagebox.showinfo("RDR1", "RDR1 archives are written by Repack (which backs up "
                                       "the original automatically) — Save FXC is not needed.")
        if not hasattr(self, 'fxc_current_file') or not self.fxc_current_file: return messagebox.showwarning("Warning", "No FXC loaded.")
        
        filepath = filedialog.asksaveasfilename(title="Save FXC", initialfile=os.path.basename(self.fxc_current_filepath), defaultextension=".fxc", filetypes=[("FXC Files", "*.fxc"), ("All Files", "*.*")])
        if not filepath: return
        
        try:
            self._log(f"Saving FXC to {filepath}...")
            
            # Create backup if overwriting existing file
            if os.path.exists(filepath):
                backup_dir = os.path.join(os.path.dirname(filepath), "backups")
                os.makedirs(backup_dir, exist_ok=True)
                backup_path = os.path.join(backup_dir, os.path.basename(filepath) + ".bak")
                shutil.copy2(filepath, backup_path)
            
            with open(filepath, 'wb') as f: 
                f.write(self.fxc_current_file.save())
                
            self.fxc_status_var.set(f"Saved: {os.path.basename(filepath)}")
            messagebox.showinfo("Success", f"Successfully saved FXC file:\n{filepath}")
            self._log("FXC saved successfully.")
            
            # Update the file size in the left tree if we saved to the same directory
            self.refresh_fxc_list()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save FXC:\n{e}")
            self._log(f"FXC Save Error: {e}")

    def _init_awc_page(self):
        page = ttk.Frame(self.page_container)
        self.pages["awc"] = page
        self.awc_file = None
        self.awc_current_shader = None
        
        # --- Info Banner ---
        banner = ttk.Frame(page, padding=10, bootstyle="danger")
        banner.pack(fill=X, pady=(0, 10))
        ttk.Label(banner, text="📦  GTA 5 Enhanced — DX12 Archives", font=("Segoe UI", 10, "bold"), bootstyle="inverse-danger").pack(side=LEFT, padx=(0, 10))
        ttk.Label(banner, text="This tab is for GTA 5 Enhanced (new version). Workflow: 1) Select an .awc on the left and click 📦 Unpack to extract every shader into /compiled/dx12/<archive>/<effect>/<stage>/. 2) Decompile, edit, recompile via the 'Compile & Decompile' tab (DX12 mode). 3) Click 📤 Repack to inject changes back into the .awc — only modified shaders are touched, a .bak is created automatically.", wraplength=900, bootstyle="inverse-danger").pack(side=LEFT, fill=X, expand=True)
        

        tools = ttk.Frame(page)
        tools.pack(fill=X, pady=(0, 10))
        
        btn_awc_open = ttk.Button(tools, text="📂 Open .awc File", command=self._awc_open, bootstyle="info")
        btn_awc_open.pack(side=LEFT, padx=(0, 5))
        hint(btn_awc_open, "Open an .awc shader library from disk.\nFiles in /awc_files/ are listed on the left.")
        btn_awc_unpack = ttk.Button(tools, text="📦 Unpack", command=self.unpack_awc, bootstyle="info")
        btn_awc_unpack.pack(side=LEFT, padx=(0, 5))
        hint(btn_awc_unpack, "Extract all shaders from the selected .awc archive(s)\ninto /compiled/dx12/<archive>/<group>/<type>/ as .cso files.\nShared shaders are placed under their first owning effect.")
        btn_awc_repack = ttk.Button(tools, text="📤 Repack", command=self.repack_awc, bootstyle="warning")
        btn_awc_repack.pack(side=LEFT, padx=(0, 15))
        hint(btn_awc_repack, "Inject modified .cso files back into the selected\n.awc archive(s). Only changed shaders are updated.\nA backup is created automatically in /awc_files/backups/.")
        btn_awc_save = ttk.Button(tools, text="💾 Save .awc As...", command=self._awc_save, bootstyle="success")
        btn_awc_save.pack(side=LEFT, padx=(0, 15))
        hint(btn_awc_save, "Rebuild and save the loaded AWC file.\nUse after importing modified shader bytecode.")
        
        btn_awc_imp = ttk.Button(tools, text="📥 Import CSO", command=self._awc_import, bootstyle="warning")
        btn_awc_imp.pack(side=LEFT, padx=(0, 5))
        hint(btn_awc_imp, "Replace the selected shader's bytecode\nwith a compiled .cso file from disk.\nRemember to Save AWC afterwards.")
        btn_awc_exp = ttk.Button(tools, text="📤 Export CSO", command=self._awc_export, bootstyle="secondary")
        btn_awc_exp.pack(side=LEFT, padx=(0, 5))
        hint(btn_awc_exp, "Export the selected shader's binary\nas a .cso file to disk.")
        btn_awc_dec = ttk.Button(tools, text="▼ Decompile to HLSL", command=self._awc_decompile, bootstyle="primary")
        btn_awc_dec.pack(side=LEFT)
        hint(btn_awc_dec, "Decompile the selected shader to .hlsl source code\nusing decomp.exe. This also restores any extra\nshader info/metadata previously hidden.")
        
        # Metadata update toggle - OFF by default (metadata rebuild can cause crashes)
        self.awc_update_metadata_var = tk.BooleanVar(value=False)
        chk_meta = ttk.Checkbutton(tools, text="Update Metadata", variable=self.awc_update_metadata_var, bootstyle="round-toggle-warning")
        chk_meta.pack(side=LEFT, padx=(15, 0))
        hint(chk_meta, "When ON, shader metadata (registers, sizes)\nis recalculated on import. Leave OFF unless\nyou know what you're doing — can cause crashes.")
        
        btn_awc_ref = ttk.Button(tools, text="⟳ Refresh", command=self.refresh_all, bootstyle="link")
        btn_awc_ref.pack(side=RIGHT)
        hint(btn_awc_ref, "Reload all file lists.")
        
        self.awc_status_var = tk.StringVar(value="Ready — No AWC loaded")
        ttk.Label(tools, textvariable=self.awc_status_var, bootstyle="secondary-inverse", padding=(5, 2)).pack(side=RIGHT, padx=10)

        panes = ttk.PanedWindow(page, orient=HORIZONTAL)
        panes.pack(fill=BOTH, expand=True)
        
        # LEFT: AWC Files in Workspace
        files_fr = ttk.Labelframe(panes, text=" AWC Files in /awc_files/ ", padding=5)
        panes.add(files_fr, weight=1)
        
        cols_f = ("size", "path")
        file_container, self.awc_file_tree = self._create_scrolled_tree(files_fr, columns=cols_f, show="headings", bootstyle="primary")
        file_container.pack(fill=BOTH, expand=True)
        self.awc_file_tree.heading("size", text="Size")
        self.awc_file_tree.column("size", width=80, anchor="e")
        self.awc_file_tree.heading("path", text="Filename")
        self.awc_file_tree.column("path", width=200)
        self.awc_file_tree.bind("<<TreeviewSelect>>", self._on_awc_file_select)
        
        # MIDDLE: Shaders inside AWC
        middle_fr = ttk.Frame(panes)
        panes.add(middle_fr, weight=2)
        
        search_fr = ttk.Frame(middle_fr)
        search_fr.pack(fill=X, pady=(0, 5))
        ttk.Label(search_fr, text="🔍 Filter:", font=("Segoe UI", 9)).pack(side=LEFT, padx=(0, 5))
        self.awc_search_var = tk.StringVar()
        self.awc_search_var.trace_add("write", lambda *args: self._awc_populate_tree())
        ttk.Entry(search_fr, textvariable=self.awc_search_var).pack(side=LEFT, fill=X, expand=True)
        
        self.awc_group_effect_var = tk.BooleanVar(value=False)
        chk_eff = ttk.Checkbutton(search_fr, text="Group by Effect", variable=self.awc_group_effect_var, command=self._awc_populate_tree, bootstyle="round-toggle-warning")
        chk_eff.pack(side=LEFT, padx=(10, 0))
        hint(chk_eff, "Group shaders by the real effect they belong to,\nas decoded from the .awc trailer (e.g. 'postfx_lut',\n'deferred_lighting', 'scaleform_shaders'). A shader\nshared by multiple effects appears under each.")

        self.awc_group_family_var = tk.BooleanVar(value=False)
        chk_fam = ttk.Checkbutton(search_fr, text="Group by Family", variable=self.awc_group_family_var, command=self._awc_populate_tree, bootstyle="round-toggle-success")
        chk_fam.pack(side=LEFT, padx=(10, 0))
        hint(chk_fam, "Bucket shaders by inferred family/category derived from\ntheir name (e.g. vehicle, ped, deferred, postfx, raytraced_*).")

        self.awc_family_coarse_var = tk.BooleanVar(value=True)
        chk_coarse = ttk.Checkbutton(search_fr, text="Coarse", variable=self.awc_family_coarse_var, command=self._awc_populate_tree, bootstyle="round-toggle-secondary")
        chk_coarse.pack(side=LEFT, padx=(4, 0))
        hint(chk_coarse, "When ON, related family tokens collapse into broad buckets\n(Vehicle*, VehicleTextured*, VehicleTransform* → vehicle).\nWhen OFF, every distinct token is its own group (~700+ groups).")
        
        cols_s = ("renohash", "type", "size")
        tree_container, self.awc_tree = self._create_scrolled_tree(middle_fr, columns=cols_s, show="tree headings", bootstyle="info")
        tree_container.pack(fill=BOTH, expand=True)
        
        self.awc_tree.heading("#0", text="Shader Hash / Name", anchor="w")
        self.awc_tree.heading("renohash", text="RenoDX Hash", anchor="w")
        self.awc_tree.heading("type", text="Type", anchor="w")
        self.awc_tree.heading("size", text="Size (Bytes)", anchor="e")
        self.awc_tree.column("#0", width=250)
        self.awc_tree.column("renohash", width=95, anchor="w")
        self.awc_tree.column("type", width=80)
        self.awc_tree.column("size", width=100, anchor="e")
        self.awc_tree.bind("<<TreeviewSelect>>", self._on_awc_select)
        
        details_fr = ttk.Labelframe(panes, text=" Shader Details ", padding=10)
        panes.add(details_fr, weight=1)
        
        self.awc_details = tk.Text(details_fr, font=("Consolas", 10), bg="#002b36", fg="#93a1a1", bd=0, wrap="word")
        self.awc_details.pack(fill=BOTH, expand=True)
        self.awc_details.insert(tk.END, "Select a shader to view\nregister bindings and metadata.")
        self.awc_details.config(state=tk.DISABLED)

    def _awc_open(self):
        filepath = filedialog.askopenfilename(title="Open AWC Shader Library", filetypes=[("AWC Files", "*.awc"), ("All Files", "*.*")])
        if not filepath: return
        self._load_awc_file(filepath)

    def _on_awc_file_select(self, event):
        sel = self.awc_file_tree.selection()
        if not sel: return
        tags = self.awc_file_tree.item(sel[0], "tags")
        if tags and len(tags) > 0:
            filepath = tags[0]
            if os.path.exists(filepath):
                self._load_awc_file(filepath)
                
    def _load_awc_file(self, filepath):
        try:
            self._log(f"Parsing AWC: {os.path.basename(filepath)}...")
            self.awc_file = parse_awc_file(filepath)
            
            # Keep track of active AWC path to easily save within workspace
            self.awc_current_filepath = filepath
            
            self.awc_status_var.set(f"Loaded: {os.path.basename(filepath)}")
            self._awc_populate_tree()
            self._log(f"Successfully loaded {self.awc_file.total_shader_count} shaders from AWC.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse AWC:\n{e}")
            self._log(f"AWC Parse Error: {e}")

    def _find_owning_effects(self, shader):
        """Return effect names that reference the given shader via the trailer index lists."""
        if not self.awc_file or not getattr(self.awc_file, 'effects', None):
            return []
        stage_arrays = [
            (self.awc_file.vertex_shaders,   "vs_indices"),
            (self.awc_file.pixel_shaders,    "ps_indices"),
            (self.awc_file.geometry_shaders, "gs_indices"),
            (self.awc_file.domain_shaders,   "ds_indices"),
            (self.awc_file.hull_shaders,     "hs_indices"),
            (self.awc_file.compute_shaders,  "cs_indices"),
        ]
        gi = None
        attr = None
        for stage_list, a in stage_arrays:
            for i, s in enumerate(stage_list):
                if s is shader:
                    gi = i
                    attr = a
                    break
            if gi is not None: break
        if gi is None:
            return []
        return [e.name for e in self.awc_file.effects if gi in getattr(e, attr, [])]

    _FAMILY_TYPE_STRIP_RE = re.compile(r'^(?:VS|PS|CS|HS|DS|GS)_?', re.IGNORECASE)
    _FAMILY_TOKEN_RE = re.compile(r'^([A-Za-z][A-Za-z0-9]*)')
    _FAMILY_HEX_RE = re.compile(r'^[0-9a-fA-F]{6,16}$')
    # Curated coarse buckets — ordered, first match wins.
    _FAMILY_COARSE_MAP = [
        (re.compile(r'^Vehicle', re.I), 'vehicle'),
        (re.compile(r'^Ped', re.I), 'ped'),
        (re.compile(r'^Fur', re.I), 'fur'),
        (re.compile(r'^PropFoliage', re.I), 'prop_foliage'),
        (re.compile(r'^north', re.I), 'rage_north'),
        (re.compile(r'^(?:point|spot)CM$|^capsule|^lightTile|^tiledLight|^directional', re.I), 'lighting'),
        (re.compile(r'^Cascade|^Shadow|^SoftShadow|^SRFTCS', re.I), 'shadows'),
        (re.compile(r'^MirrorReflection|^Reflection', re.I), 'reflection'),
        (re.compile(r'^Deferred', re.I), 'deferred'),
        (re.compile(r'^Transform', re.I), 'transform'),
        (re.compile(r'^Textured', re.I), 'forward_textured'),
        (re.compile(r'^Blit', re.I), 'blit'),
        (re.compile(r'^Clear', re.I), 'clear'),
        (re.compile(r'^Rage', re.I), 'rage_im'),
        (re.compile(r'^Cloud', re.I), 'clouds'),
        (re.compile(r'^Sky', re.I), 'sky'),
        (re.compile(r'^DOF|^DepthOfField|^adaptiveDof|^dofCompute', re.I), 'depth_of_field'),
        (re.compile(r'^Foam|^River|^Water|^puddle', re.I), 'water'),
        (re.compile(r'^BreakableGlass|^Glass', re.I), 'glass'),
        (re.compile(r'^Passthrough|^PassThrough', re.I), 'passthrough'),
        (re.compile(r'^postfx', re.I), 'postfx'),
        (re.compile(r'^bloom|^FSR|^TemporalAA|^ReactiveMask|^BiasCurrentColor', re.I), 'postfx'),
        (re.compile(r'^ssdo|^HDAO|^MRSSAO|^screenTransformS?SAO|^screenTransformDepthSSAO', re.I), 'ambient_occlusion'),
        (re.compile(r'^raytraced|^bvh', re.I), 'raytraced'),
        (re.compile(r'^volumeShaft|^lightShaft', re.I), 'lightshafts'),
        (re.compile(r'^ptx', re.I), 'particles'),
        (re.compile(r'^Vfx', re.I), 'vfx'),
        (re.compile(r'^texturecompression', re.I), 'texture_tools'),
    ]

    @classmethod
    def _extract_family_group(cls, name, coarse=True):
        """Derive a family/category bucket from a shader name.
        Returns (group_key, is_namespaced). is_namespaced=True for explicit `group:Name` shaders.
        """
        if ':' in name:
            return name.split(':', 1)[0], True
        rest = cls._FAMILY_TYPE_STRIP_RE.sub('', name, count=1)
        m = cls._FAMILY_TOKEN_RE.match(rest)
        if not m:
            return '_untitled', False
        tok = m.group(1)
        if cls._FAMILY_HEX_RE.fullmatch(tok):
            return '_untitled', False
        if coarse:
            for pat, bucket in cls._FAMILY_COARSE_MAP:
                if pat.match(tok):
                    return bucket, False
        return tok, False

    def _awc_populate_tree(self):
        self.awc_tree.delete(*self.awc_tree.get_children())
        if not self.awc_file: return
        
        search_term = ""
        if hasattr(self, 'awc_search_var'):
            search_term = self.awc_search_var.get().lower()
        
        group_by_effect = hasattr(self, 'awc_group_effect_var') and self.awc_group_effect_var.get()
        group_by_family = hasattr(self, 'awc_group_family_var') and self.awc_group_family_var.get()
        family_coarse = not hasattr(self, 'awc_family_coarse_var') or self.awc_family_coarse_var.get()

        categories = [
            ("Vertex Shaders", self.awc_file.vertex_shaders, "VS"),
            ("Pixel Shaders", self.awc_file.pixel_shaders, "PS"),
            ("Compute Shaders", self.awc_file.compute_shaders, "CS"),
            ("Geometry Shaders", self.awc_file.geometry_shaders, "GS"),
            ("Hull Shaders", self.awc_file.hull_shaders, "HS"),
            ("Domain Shaders", self.awc_file.domain_shaders, "DS")
        ]

        self.awc_shader_map = {}

        if group_by_effect and getattr(self.awc_file, 'effects', None):
            # Build (stage_array, type_label) tuples ordered to match Effect index lists.
            stage_arrays = [
                (self.awc_file.vertex_shaders, "VS", "vs_indices"),
                (self.awc_file.pixel_shaders,  "PS", "ps_indices"),
                (self.awc_file.geometry_shaders, "GS", "gs_indices"),
                (self.awc_file.domain_shaders, "DS", "ds_indices"),
                (self.awc_file.hull_shaders,   "HS", "hs_indices"),
                (self.awc_file.compute_shaders,"CS", "cs_indices"),
            ]
            referenced_globals = set()
            for eff in self.awc_file.effects:
                members = []
                for stage_list, sh_type, attr in stage_arrays:
                    for gi in getattr(eff, attr, []):
                        if 0 <= gi < len(stage_list):
                            s = stage_list[gi]
                            if search_term and search_term not in s.name.lower() and search_term not in f"0x{s.hash:016x}":
                                continue
                            members.append((s, sh_type, gi, attr))
                            referenced_globals.add((attr, gi))
                if not members:
                    continue
                types_in_group = sorted({t for _, t, _, _ in members})
                type_summary = "/".join(types_in_group)
                techs = getattr(eff, 'techniques', None) or []
                tech_count = len(techs)
                pass_count = sum(len(getattr(t, 'passes', [])) for t in techs)
                label = f"📦 {eff.name} ({len(members)} shaders"
                if tech_count:
                    label += f", {tech_count} techs, {pass_count} passes"
                label += ")"
                group_id = self.awc_tree.insert("", "end", text=label, values=("", type_summary, ""), open=False)
                for s, sh_type, gi, _ in sorted(members, key=lambda x: (x[1], x[0].name.lower())):
                    item_id = self.awc_tree.insert(group_id, "end", text=s.name, values=(renodx_hash(s.shader_binary), sh_type, f"{s.size:,}"))
                    self.awc_shader_map[item_id] = s

            # Orphans: any shader not referenced by any effect.
            orphans = []
            for stage_list, sh_type, attr in stage_arrays:
                for gi, s in enumerate(stage_list):
                    if (attr, gi) in referenced_globals:
                        continue
                    if search_term and search_term not in s.name.lower() and search_term not in f"0x{s.hash:016x}":
                        continue
                    orphans.append((s, sh_type))
            if orphans:
                orphan_id = self.awc_tree.insert("", "end", text=f"❓ Unreferenced ({len(orphans)})", values=("", "", ""), open=False)
                for s, sh_type in sorted(orphans, key=lambda x: (x[1], x[0].name.lower())):
                    item_id = self.awc_tree.insert(orphan_id, "end", text=s.name, values=(renodx_hash(s.shader_binary), sh_type, f"{s.size:,}"))
                    self.awc_shader_map[item_id] = s
        elif group_by_family:
            all_shaders = []
            for cat_name, shaders, sh_type in categories:
                for s in shaders:
                    if search_term in s.name.lower() or search_term in f"0x{s.hash:016x}":
                        all_shaders.append((s, sh_type))

            from collections import defaultdict
            family_groups = defaultdict(list)
            namespaced = set()
            for s, sh_type in all_shaders:
                key, is_ns = self._extract_family_group(s.name, coarse=family_coarse)
                family_groups[key].append((s, sh_type))
                if is_ns:
                    namespaced.add(key)

            # Sort: explicit namespaces first (alpha), then by descending count, _untitled last
            def sort_key(kv):
                k, v = kv
                if k == '_untitled':
                    return (3, 0, k)
                if k in namespaced:
                    return (0, k.lower(), 0)
                return (1, -len(v), k.lower())

            for key, group in sorted(family_groups.items(), key=sort_key):
                types_in_group = sorted(set(t for _, t in group))
                type_summary = "/".join(types_in_group)
                icon = "📛" if key in namespaced else ("❓" if key == '_untitled' else "📦")
                label = "Untitled / Hash-only" if key == '_untitled' else key
                group_id = self.awc_tree.insert("", "end", text=f"{icon} {label} ({len(group)})", values=("", type_summary, ""), open=False)
                for s, sh_type in sorted(group, key=lambda x: x[0].name.lower()):
                    item_id = self.awc_tree.insert(group_id, "end", text=s.name, values=(renodx_hash(s.shader_binary), sh_type, f"{s.size:,}"))
                    self.awc_shader_map[item_id] = s
        else:
            # Default: group by shader type category
            for cat_name, shaders, sh_type in categories:
                if not shaders: continue
                
                filtered = []
                for s in shaders:
                    if search_term in s.name.lower() or search_term in f"0x{s.hash:016x}":
                        filtered.append(s)
                        
                if not filtered: continue
                
                cat_id = self.awc_tree.insert("", "end", text=f"{cat_name} ({len(filtered)})", values=("", "", ""), open=True)
                for s in filtered:
                    item_id = self.awc_tree.insert(cat_id, "end", text=s.name, values=(renodx_hash(s.shader_binary), sh_type, f"{s.size:,}"))
                    self.awc_shader_map[item_id] = s

    def _on_awc_select(self, event):
        sel = self.awc_tree.selection()
        if not sel: return
        item_id = sel[0]
        shader = self.awc_shader_map.get(item_id)
        if shader:
            self.awc_current_shader = shader
            
            details = f"Name: {shader.name}\n"
            details += f"RenoDX Hash: {renodx_hash(shader.shader_binary)}\n"
            details += f"SGA Hash: 0x{shader.hash:016X}\n"
            details += f"Binary Size: {shader.size:,} bytes\n"
            wavesize_desc = {50: "Wave32", 65: "Wave64 (preferred)", 66: "Wave64 (required)"}.get(shader.wavesize, f"Unknown")
            details += f"Wave Size: {wavesize_desc} (0x{shader.wavesize:02X})\n"
            from awclib.models import ResourceType
            tex_count = sum(1 for r in shader.registers if ResourceType.get_register_prefix(r.resource_type) == "t")
            sampler_count = sum(1 for r in shader.registers if ResourceType.get_register_prefix(r.resource_type) == "s")
            cbv_count = sum(1 for r in shader.registers if ResourceType.get_register_prefix(r.resource_type) == "b")
            uav_count = sum(1 for r in shader.registers if ResourceType.get_register_prefix(r.resource_type) == "u")
            details += f"Registers: {shader.reg_count} | CBVs: {cbv_count} | Textures: {tex_count} | Samplers: {sampler_count} | UAVs: {uav_count}\n"

            owning_effects = self._find_owning_effects(shader)
            if owning_effects:
                details += f"Effects: {', '.join(owning_effects)}\n"
            details += "="*40 + "\n"
            
            for reg in shader.registers:
                prefix = ResourceType.get_register_prefix(reg.resource_type)
                res_type_name = ResourceType.get_name(reg.resource_type)
                
                binding = f"{prefix}{reg.register_slot}"
                if reg.register_space > 0: binding += f", space{reg.register_space}"
                
                desc = f"Register [{binding}]: {reg.reg_name} ({res_type_name})"
                if prefix == "b": desc += f" (CBs: {reg.cbuffer_count})"
                details += f"{desc}\n"
                for cb in reg.cbuffers:
                    details += f"   ► {cb.type_name} {cb.cbuffer_name}[{cb.array_size}]\n"
                
            self.awc_details.config(state=tk.NORMAL)
            self.awc_details.delete("1.0", tk.END)
            self.awc_details.insert(tk.END, details)
            self.awc_details.config(state=tk.DISABLED)
        else:
            self.awc_current_shader = None
            self.awc_details.config(state=tk.NORMAL)
            self.awc_details.delete("1.0", tk.END)
            self.awc_details.config(state=tk.DISABLED)

    def _awc_import(self):
        if not self.awc_file: return messagebox.showwarning("Warning", "Open an AWC file first.")
        if not self.awc_current_shader: return messagebox.showwarning("Warning", "Select a shader from the list first.")
        
        shader = self.awc_current_shader
        filepath = filedialog.askopenfilename(title=f"Import CSO for {shader.name}", initialdir=self._get_current_dir("compiled"), filetypes=[("Compiled Shaders", "*.cso"), ("All Files", "*.*")])
        if not filepath: return
        
        try:
            from awclib.awc_writer import import_shader
            
            # Find shader type and index
            type_map = {
                'vertex': self.awc_file.vertex_shaders,
                'pixel': self.awc_file.pixel_shaders,
                'geometry': self.awc_file.geometry_shaders,
                'domain': self.awc_file.domain_shaders,
                'hull': self.awc_file.hull_shaders,
                'compute': self.awc_file.compute_shaders,
            }
            
            shader_type = None
            shader_index = None
            for stype, slist in type_map.items():
                for i, s in enumerate(slist):
                    if s is shader:
                        shader_type = stype
                        shader_index = i
                        break
                if shader_type: break
            
            if shader_type is None:
                messagebox.showerror("Error", "Could not find shader in AWC file.")
                return
            
            success, msg = import_shader(self.awc_file, shader_type, shader_index, filepath, 
                                         update_metadata=self.awc_update_metadata_var.get())
            
            if success:
                messagebox.showinfo("Success", f"{msg}\nDon't forget to Save AWC.")
                self._log(f"AWC: {msg}")
            else:
                messagebox.showerror("Error", f"Import failed:\n{msg}")
                return
            
            # Refresh the tree item size
            sel = self.awc_tree.selection()
            if sel:
                vals = self.awc_tree.item(sel[0], "values")
                if vals:
                    self.awc_tree.item(sel[0], values=(renodx_hash(shader.shader_binary), vals[1], f"{shader.size:,}"))
            
            # Refresh the details panel
            self._on_awc_select(None)
                    
        except Exception as e:
            import traceback
            traceback.print_exc()
            messagebox.showerror("Error", f"Failed to import CSO:\n{e}")

    def _awc_export(self):
        if not self.awc_file: return messagebox.showwarning("Warning", "Open an AWC file first.")
        if not self.awc_current_shader: return messagebox.showwarning("Warning", "Select a shader from the list first.")
        
        shader = self.awc_current_shader
        safe_name = shader.name.replace("\\", "_").replace("/", "_").replace(":", "_")[:80]
        filepath = filedialog.asksaveasfilename(title=f"Export {safe_name} as CSO", initialdir=self._get_current_dir("compiled"), defaultextension=".cso", initialfile=f"{safe_name}.cso", filetypes=[("Compiled Shaders", "*.cso"), ("All Files", "*.*")])
        if not filepath: return
        
        try:
            with open(filepath, "wb") as f:
                f.write(shader.shader_binary)
            self._log(f"AWC: Exported {shader.name} to CSO.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export CSO:\n{e}")

    def _awc_save(self):
        if not self.awc_file: return messagebox.showwarning("Warning", "No AWC loaded.")
        
        filepath = filedialog.asksaveasfilename(title="Save AWC Shader Library", defaultextension=".awc", filetypes=[("AWC Files", "*.awc"), ("All Files", "*.*")])
        if not filepath: return
        
        try:
            self._log(f"Rebuilding AWC to {filepath}...")
            writer = AWCRebuilder(self.awc_file, self.awc_current_filepath)
            writer.write(filepath)
            self.awc_status_var.set(f"Saved: {os.path.basename(filepath)}")
            messagebox.showinfo("Success", f"Successfully saved rebuild AWC file:\n{filepath}")
            self._log("AWC saved successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to rebuild AWC:\n{e}")
            self._log(f"AWC Save Error: {e}")

    def _awc_decompile(self):
        if not self.awc_file: return messagebox.showwarning("Warning", "Open an AWC file first.")
        if not self.awc_current_shader: return messagebox.showwarning("Warning", "Select a shader from the list first.")
        
        shader = self.awc_current_shader
        out_dir = self._get_current_dir("decompiled")
        tools_dir = self.dirs.get("compilers", "")
        
        self._log(f"AWC: Decompiling {shader.name}...")
        
        def run_decomp():
            success, msg, out_path = decompile_shader(shader, out_dir, tools_dir)
            self.root.after(0, self._awc_decompile_done, success, msg, out_path)
            
        threading.Thread(target=run_decomp, daemon=True).start()

    def _awc_decompile_done(self, success, msg, out_path):
        if success:
            self._log(f"AWC Decompile Success: {msg}")
            
            # Show ASM/HLSL window like the normal dev page does
            if out_path and os.path.exists(out_path):
                with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                AsmWindow(self.root, os.path.basename(out_path), content)
        else:
            self._log(f"AWC Decompile Failed: {msg}")
            messagebox.showerror("Decompile Error", msg)

    # --- HELPERS ---
    def _select_all(self, tree):
        items = []
        for child in tree.get_children():
            items.append(child)
            children = tree.get_children(child)
            if children: 
                tree.item(child, open=True)
                items.extend(children)
        if items:
            tree.selection_set(items)

    def _get_type_folder(self, prof):
        p = prof.lower()
        if p.startswith("vs") or "_vs" in p or "+vs" in p: return "VS"
        if p.startswith("ps") or "_ps" in p or "+ps" in p: return "PS"
        if p.startswith("cs") or "_cs" in p or "+cs" in p: return "CS"
        if p.startswith("ds") or "_ds" in p or "+ds" in p: return "DS"
        if p.startswith("gs") or "_gs" in p or "+gs" in p: return "GS"
        if p.startswith("hs") or "_hs" in p or "+hs" in p: return "HS"
        return "Misc"

    def _detect_profile(self, path):
        # Check filename for explicit profile (e.g. .vs_6_0.hlsl, .ps_6_5.hlsl, .ps_6_6.hlsl)
        fn = os.path.basename(path).lower()
        # SM 6.6
        if "vs_6_6" in fn: return "vs_6_6"
        if "ps_6_6" in fn: return "ps_6_6"
        if "cs_6_6" in fn: return "cs_6_6"
        if "gs_6_6" in fn: return "gs_6_6"
        if "ds_6_6" in fn: return "ds_6_6"
        if "hs_6_6" in fn: return "hs_6_6"
        # SM 6.5
        if "vs_6_5" in fn: return "vs_6_5"
        if "ps_6_5" in fn: return "ps_6_5"
        if "cs_6_5" in fn: return "cs_6_5"
        if "gs_6_5" in fn: return "gs_6_5"
        if "ds_6_5" in fn: return "ds_6_5"
        if "hs_6_5" in fn: return "hs_6_5"
        # SM 6.0
        if "vs_6_0" in fn: return "vs_6_0"
        if "ps_6_0" in fn: return "ps_6_0"
        if "cs_6_0" in fn: return "cs_6_0"
        if "gs_6_0" in fn: return "gs_6_0"
        if "ds_6_0" in fn: return "ds_6_0"
        if "hs_6_0" in fn: return "hs_6_0"
        
        # No explicit shader model in the filename. Pick the default profile for
        # the active DX mode: DX12/dxc requires SM6+ (it cannot compile *_5_0),
        # and GTA5 Enhanced compute shaders are uniformly cs_6_6.
        ver = self.dx_version.get()
        def _prof(stage):
            if ver == "dx12":
                return f"{stage}_6_6" if stage == "cs" else f"{stage}_6_0"
            if ver == "rdr1":
                # RDR1 is uniformly SM 6.0 (verified across terrain/postfx).
                return f"{stage}_6_0"
            return f"{stage}_5_0"

        folder = os.path.basename(os.path.dirname(path)).upper()
        if folder in ("VS", "PS", "CS", "DS", "GS", "HS"):
            return _prof(folder.lower())

        if fn.startswith("vs") or "_vs" in fn or "+vs" in fn or ".vsh" in fn: return _prof("vs")
        if fn.startswith("ps") or "_ps" in fn or "+ps" in fn or ".psh" in fn: return _prof("ps")
        if fn.startswith("cs") or "_cs" in fn or "+cs" in fn or ".csh" in fn: return _prof("cs")
        if fn.startswith("ds") or "_ds" in fn: return _prof("ds")
        if fn.startswith("gs") or "_gs" in fn: return _prof("gs")
        if fn.startswith("hs") or "_hs" in fn: return _prof("hs")

        # Fallback: detect shader type from file content
        content_type = self._detect_shader_type_content(path)
        if content_type == "[CS]": return _prof("cs")
        if content_type == "[VS]": return _prof("vs")
        if content_type == "[PS]": return _prof("ps")
        return _prof("ps")

    def _detect_shader_type_content(self, path):
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Compute shaders carry a [numthreads(...)] attribute and have no
            # SV_Position / SV_Target semantics, so detect them first.
            if re.search(r'\[\s*numthreads\s*\(', content, re.IGNORECASE):
                return "[CS]"

            # Heuristic from compile_shaders.py
            if "SV_Position" in content and "SV_Target" in content:
                if re.search(r'out\s+[\w\d\s]+\s*:\s*SV_Position', content, re.IGNORECASE):
                    return "[VS]"
                if re.search(r':\s*SV_Position', content) and not re.search(r'out\s+.*SV_Position', content):
                     if re.search(r':\s*SV_Target', content) or re.search(r'out\s+.*SV_Target', content):
                         return "[PS]"
            
            if "SV_Position" in content and "SV_Target" not in content:
                if re.search(r'out\s+[\w\d\s]+\s*:\s*SV_Position', content, re.IGNORECASE):
                     return "[VS]"
            
            if "SV_Target" in content:
                return "[PS]"

            if "out float4 o5 : SV_Position0" in content:
                return "[VS]"
                
        except: pass
        return ""

    def _log(self, txt): self.msg_queue.put(("LOG", txt))
    
    def _process_queue(self):
        try:
            while True:
                kind, data = self.msg_queue.get_nowait()
                if kind == "LOG":
                    self.log_widget.config(state=tk.NORMAL)
                    ts = time.strftime("[%H:%M:%S] ")
                    self.log_widget.insert(END, ts, "ts")
                    self.log_widget.insert(END, data + "\n")
                    self.log_widget.tag_config("ts", foreground="#586e75")
                    self.log_widget.see(END)
                    self.log_widget.config(state=tk.DISABLED)
                elif kind == "P_START":
                    self.progress['maximum'] = data; self.progress['value'] = 0
                    self.status_badge.config(text="Processing...", bootstyle="warning-inverse")
                    self.is_processing = True
                elif kind == "P_STEP": self.progress['value'] += 1
                elif kind == "P_STOP": 
                    self.progress['value'] = 0
                    self.status_badge.config(text="Ready", bootstyle="success-inverse")
                    self.is_processing = False
                elif kind == "REFRESH": self.refresh_all()
        except queue.Empty: pass
        self.root.after(100, self._process_queue)

    def refresh_all(self):
        self.refresh_source()
        self.refresh_compiled()
        self.refresh_fxc_list()
        self.refresh_awc_list()
        
    def refresh_awc_list(self):
        if not hasattr(self, 'awc_file_tree'): return
        self.awc_file_tree.delete(*self.awc_file_tree.get_children())
        d = self.dirs.get("awc", "")
        if os.path.exists(d):
            for f in os.listdir(d):
                if f.lower().endswith(".awc"):
                    path = os.path.join(d, f)
                    sz = f"{os.path.getsize(path)/1024/1024:.2f} MB"
                    self.awc_file_tree.insert("", "end", values=(sz, f), tags=(path,))

    def _on_dx_change(self):
        ver = self.dx_version.get()
        # DX12-family profiles (Enhanced + RDR1) use dxc/SM6 and expose the
        # decompile-method selector. RDR1 additionally uses the FXC tab (its
        # rgxd .fxc archives), while Enhanced uses the AWC tab.
        dx12_family = ver in ("dx12", "rdr1")

        # AWC tab: Enhanced only.
        if hasattr(self, 'btn_awc'):
            if ver == "dx12":
                self.btn_awc.pack(fill=X, pady=2, after=self.btn_dev)
            else:
                self.btn_awc.pack_forget()

        # FXC tab: Legacy (DX11) and RDR1.
        if ver == "dx12":
            self.btn_fxc.pack_forget()
        else:
            self.btn_fxc.pack(fill=X, pady=2, after=self.btn_dev)

        # Decompile-method selector: DX12-family only.
        if hasattr(self, 'fr_decomp_method') and hasattr(self, 'sep_view_mode'):
            if dx12_family:
                self.fr_decomp_method.pack(side=LEFT, before=self.sep_view_mode, padx=(0, 10))
            else:
                self.fr_decomp_method.pack_forget()

        # Leave a page that no longer applies to the active profile.
        if self.current_page == "fxc" and ver == "dx12":
            self._show_page("dev")
        if self.current_page == "awc" and ver != "dx12":
            self._show_page("dev")

        # Retarget the FXC tab banner/handlers for RDR1 vs Legacy.
        self._update_fxc_mode_ui()
        self.refresh_all()

    def _update_fxc_mode_ui(self):
        """Switch the FXC tab's banner text between GTA5 Legacy and RDR1, and
        drop any archive loaded under the previous profile so the DX11 parser
        never sees rgxd data (and vice versa)."""
        if not hasattr(self, 'fxc_banner_title'):
            return
        # Clear loaded-archive state from the other profile.
        self.fxc_current_file = None
        self.fxc_is_rdr1 = False
        self._fxc_rdr1_data = None
        self.fxc_current_shader = None
        if hasattr(self, 'fxc_shader_tree'):
            self.fxc_shader_tree.delete(*self.fxc_shader_tree.get_children())
        if hasattr(self, 'fxc_status_var'):
            self.fxc_status_var.set("Ready — No FXC selected")
        if self.dx_version.get() == "rdr1":
            self.fxc_banner_title.config(text="📦  RDR1 — DX12 rgxd .fxc Archives")
            self.fxc_banner_body.config(
                text="This tab is for Red Dead Redemption (PC). Select an RDR1 .fxc "
                     "archive on the left, then: 1) Unpack to extract SM6.0 shaders as .cso,  "
                     "2) Go to 'Compile & Decompile' (RDR1 profile) to decompile — I/O "
                     "semantics are auto-restored — edit and recompile,  3) Repack here to "
                     "splice your changes back in. Size changes are handled automatically.")
        else:
            self.fxc_banner_title.config(text="📦  GTA 5 Legacy — DX11 Archives")
            self.fxc_banner_body.config(
                text="This tab is for GTA 5 Legacy (older version). Select a .fxc archive file "
                     "on the left, then: 1) Unpack it to extract shaders,  2) Go to 'Compile & "
                     "Decompile' to edit and recompile,  3) Come back here and Repack to inject "
                     "your changes back into the archive.")

    def refresh_source(self):
        self.src_tree.delete(*self.src_tree.get_children())
        self._filter_source()

    def _filter_source(self, *args):
        self.src_tree.delete(*self.src_tree.get_children())
        search = self.search_var.get().lower()
        mode = self.source_mode_var.get()
        root = self._get_current_dir("source") if mode == "source" else self._get_current_dir("decompiled")
        
        groups = {}
        for dp, _, fn in os.walk(root):
            if ".git" in dp or "__pycache__" in dp: continue
            for f in sorted(fn):
                if f.endswith(".hlsl"):
                    if search and search not in f.lower(): continue
                    full = os.path.join(dp, f)
                    rel_path = os.path.relpath(dp, root)
                    if rel_path == ".":
                        parts = f.split('+')
                        if len(parts) >= 3: 
                            grp = parts[1]
                            name = f"[{parts[0]}] {'+'.join(parts[2:])}"
                        elif len(parts) == 2:
                            grp = parts[0]
                            name = parts[1]
                        else:
                            grp = "Misc"
                            name = f
                    else:
                        grp = rel_path
                        parts = f.split('+')
                        if len(parts) >= 2 and (parts[0].isalnum() or parts[0] in self.hash_map):
                            name = f"[{parts[0]}] {'+'.join(parts[1:])}"
                        else:
                            name = f
                    
                    # Detect shader type if not explicitly known from filename/folder
                    # (only if we didn't already determine it's a VS/PS etc from the filename, but even then, content check is more robust for 0x files)
                    # Let's add the tag.
                    type_tag = self._detect_shader_type_content(full)
                    if type_tag:
                         name = f"{type_tag} {name}"

                    tags = (full,)
                    if grp not in groups: 
                        groups[grp] = self.src_tree.insert("", "end", text=f" 📂 {grp}", open=True)
                    icon = "⚡" if mode == "source" else "📄"
                    self.src_tree.insert(groups[grp], "end", text=f"   {icon} {name}", tags=tags)

    def refresh_compiled(self):
        self.cmp_tree.delete(*self.cmp_tree.get_children())
        root = self._get_current_dir("compiled")
        groups = {}
        for dp, _, fn in os.walk(root):
            grp = os.path.relpath(dp, root)
            if grp == ".": grp = "Root"
            
            # Create group if files exist or it's a subdirectory (not checking files yet, but good to have groups)
            # Actually, let's only create group if there are matching files to avoid empty folders clunk
            has_cso = any(f.endswith(".cso") for f in fn)
            if has_cso:
                if grp not in groups: 
                    groups[grp] = self.cmp_tree.insert("", "end", text=f" 📁 {grp}", open=True)
                
                for f in sorted(fn):
                    if f.endswith(".cso"):
                        self.cmp_tree.insert(groups[grp], "end", text=f"   ⚙ {f}", tags=(os.path.join(dp, f),))

    def refresh_fxc_list(self):
        self.fxc_tree.delete(*self.fxc_tree.get_children())
        d = self.dirs["fxc"]
        if os.path.exists(d):
            search_term = ""
            if hasattr(self, 'fxc_search_var'):
                search_term = self.fxc_search_var.get().lower()
                
            items_to_parse = []
            for f in os.listdir(d):
                if f.lower().endswith(".fxc"):
                    if search_term and search_term not in f.lower(): continue
                    path = os.path.join(d, f)
                    sz = f"{os.path.getsize(path)/1024:.1f} KB"
                    item_id = self.fxc_tree.insert("", "end", values=(sz, "...", f), tags=(path,))
                    items_to_parse.append((item_id, path))
                    
            if items_to_parse:
                threading.Thread(target=self._worker_parse_fxc_counts, args=(items_to_parse,), daemon=True).start()

    def _worker_parse_fxc_counts(self, items):
        rdr1_mode = self.dx_version.get() == "rdr1"
        for item_id, path in items:
            try:
                with open(path, 'rb') as f: data = f.read()
                if rdr1_mode:
                    # rgxd container: count embedded DXBC/DXIL blobs.
                    count = len(rdr1_fxc.scan(data))
                else:
                    fxc = fxc_parser.FxcFile()
                    fxc.load(data)
                    count = sum(len(grp.Shaders) for grp in fxc.ShaderGroups if grp.Shaders)

                # UI update thread safe
                self.root.after(0, lambda i=item_id, c=count: self._update_fxc_count(i, c))
            except Exception: pass
            
    def _update_fxc_count(self, item_id, count):
        if self.fxc_tree.exists(item_id):
            vals = self.fxc_tree.item(item_id, "values")
            if vals:
                self.fxc_tree.item(item_id, values=(vals[0], f"{count:,}", vals[2]))

    def _open_src_file(self, event):
        item = self.src_tree.identify_row(event.y)
        if item:
            tags = self.src_tree.item(item, "tags")
            if tags:
                self.ctx_item_tags = tags
                self._open_editor()

    def _ctx_menu(self, event, tree, mtype):
        item = tree.identify_row(event.y)
        if item:
            tags = tree.item(item, "tags")
            if tags:
                tree.selection_set(item)
                self.ctx_item_tags = tags
                menu = self.ctx_src if mtype == "src" else self.ctx_cmp
                menu.post(event.x_root, event.y_root)

    def _ctx_open_folder(self):
        if hasattr(self, 'ctx_item_tags'):
            subprocess.Popen(f'explorer /select,"{os.path.abspath(self.ctx_item_tags[0])}"')

    def _open_editor(self):
        if hasattr(self, 'ctx_item_tags'):
            path = os.path.abspath(self.ctx_item_tags[0])
        else:
            sel = self.src_tree.selection()
            if not sel: return
            tags = self.src_tree.item(sel[0], "tags")
            if not tags: return
            path = os.path.abspath(tags[0])
        ed = self.config["Paths"]["editor_path"]
        if ed and os.path.exists(ed): subprocess.Popen([ed, path])
        else: os.startfile(path)

    def organize_source_files(self):
        src_dir = self._get_current_dir("source")
        moves = []
        count = 0
        self._log("Analyzing files for reorganization...")
        for root, _, files in os.walk(src_dir):
            for f in files:
                if not f.endswith(".hlsl"): continue
                parts = f.split('+')
                group = None
                new_filename = f
                if len(parts) >= 3: 
                    group = parts[1]
                    new_filename = f"{parts[0]}+{'+'.join(parts[2:])}"
                elif len(parts) == 2: 
                    group = parts[0]
                    new_filename = parts[1]
                if not group: continue
                lower_f = f.lower()
                sh_type = "Misc"
                if any(x in lower_f for x in ["_vs", "+vs", "vsh"]): sh_type = "VS"
                elif any(x in lower_f for x in ["_ps", "+ps", "psh"]): sh_type = "PS"
                elif any(x in lower_f for x in ["_cs", "+cs", "csh"]): sh_type = "CS"
                elif any(x in lower_f for x in ["_ds", "+ds", "dsh"]): sh_type = "DS"
                elif any(x in lower_f for x in ["_gs", "+gs", "gsh"]): sh_type = "GS"
                elif any(x in lower_f for x in ["_hs", "+hs", "hsh"]): sh_type = "HS"
                target_folder = os.path.join(src_dir, group, sh_type)
                current_path = os.path.join(root, f)
                target_path = os.path.join(target_folder, new_filename)
                if os.path.exists(target_path) and os.path.abspath(current_path) != os.path.abspath(target_path):
                    self._log(f"Skipping {f}: Destination already exists.")
                    continue
                if os.path.abspath(current_path) != os.path.abspath(target_path):
                    moves.append((current_path, target_folder, target_path))
        for curr, t_folder, t_path in moves:
            try:
                os.makedirs(t_folder, exist_ok=True)
                shutil.move(curr, t_path)
                count += 1
            except Exception as e:
                self._log(f"Error moving {os.path.basename(curr)}: {e}")
        if count > 0:
            self._log(f"Organized and renamed {count} files.")
            self.refresh_source()
        else:
            self._log("No files needed organization.")

    def _toggle_watcher(self):
        if self.watcher_var.get():
            self._log("Watcher started.")
            self.watcher_running = True
            threading.Thread(target=self._watcher_loop, daemon=True).start()
        else:
            self._log("Watcher stopped.")
            self.watcher_running = False

    def _watcher_loop(self):
        watched = {}
        while self.watcher_running:
            for root, _, files in os.walk(self._get_current_dir("source")):
                for f in files:
                    if f.endswith(".hlsl"):
                        p = os.path.join(root, f)
                        try:
                            mt = os.stat(p).st_mtime
                            if p not in watched: watched[p] = mt
                            elif mt > watched[p]:
                                watched[p] = mt
                                self._log(f"Auto-compile: {f}")
                                prof = self._detect_profile(p)
                                self._run_compile([(p, prof)])
                        except: pass
            time.sleep(1)

    def compile_async(self):
        sel = self.src_tree.selection()
        tasks = []
        for i in sel:
            tags = self.src_tree.item(i, "tags")
            if tags:
                p = tags[0]
                prof = self._detect_profile(p)
                tasks.append((p, prof))
        if tasks:
            self.msg_queue.put(("P_START", len(tasks)))
            threading.Thread(target=self._run_compile, args=(tasks,)).start()

    def _run_compile(self, tasks):
        for path, prof in tasks:
            fn = os.path.basename(path)
            self._log(f"Compiling {fn}...")
            src_root = self._get_current_dir("source") if self.source_mode_var.get() == "source" else self._get_current_dir("decompiled")
            try:
                rel_path = os.path.relpath(path, src_root)
                rel_dir = os.path.dirname(rel_path)
            except: rel_dir = ""
            parts = fn.split('+')
            out_paths = []
            shader_hash = None
            shader_group = None
            if rel_dir and rel_dir != ".":
                path_parts = rel_dir.split(os.sep)
                if len(path_parts) >= 1: shader_group = path_parts[0]
                if len(parts) >= 2:
                    potential_hash = parts[0]
                    if potential_hash in self.hash_map or potential_hash.isalnum():
                        shader_hash = potential_hash
            else:
                if len(parts) >= 3: 
                    shader_hash = parts[0]
                    shader_group = parts[1]
                elif len(parts) == 2: 
                    shader_group = parts[0]
            if shader_hash and shader_hash in self.hash_map:
                self._log(f"  -> Hash identified: {shader_hash}")
                base_name = os.path.splitext(parts[-1])[0]
                for g in self.hash_map[shader_hash]:
                    type_folder = self._get_type_folder(prof)
                    smart = os.path.join(self._get_current_dir("compiled"), g, type_folder)
                    if os.path.exists(smart):
                        out_paths.append(os.path.join(smart, base_name + ".cso"))
                    else:
                        simp = os.path.join(self._get_current_dir("compiled"), g)
                        os.makedirs(simp, exist_ok=True)
                        out_paths.append(os.path.join(simp, base_name + ".cso"))
            if shader_group:
                base_name = os.path.splitext(parts[-1])[0]
                type_folder = self._get_type_folder(prof)
                target_dir = os.path.join(self._get_current_dir("compiled"), shader_group, type_folder)
                if os.path.exists(target_dir):
                    out_paths.append(os.path.join(target_dir, base_name + ".cso"))
            if not out_paths:
                target_dir = os.path.join(self._get_current_dir("compiled"), rel_dir)
                os.makedirs(target_dir, exist_ok=True)
                out_name = os.path.splitext(fn)[0] + ".cso"
                out_paths.append(os.path.join(target_dir, out_name))
            tmp = os.path.join(self._get_current_dir("compiled"), "_temp.cso")
            
            cmd = []
            if self.dx_version.get() in ("dx12", "rdr1"):
                # dxc only supports Shader Model 6.0+. If a *_5_0 profile slipped
                # through (e.g. an explicitly named legacy file compiled in DX12
                # mode), upgrade it so the compile doesn't fail outright:
                # compute -> 6_6 (the SM the game uses), everything else -> 6_0.
                # RDR1 ships SM6.0 exclusively (verified: ps_6_0/vs_6_0), so its
                # default profiles are 6_0 (see _detect_profile).
                dxc_prof = prof
                if dxc_prof.endswith("_5_0"):
                    stage = dxc_prof.split("_", 1)[0]
                    dxc_prof = f"{stage}_6_6" if stage == "cs" else f"{stage}_6_0"
                    self._log(f"  -> Upgraded {prof} to {dxc_prof} for DXC (SM6+ required)")
                # Entry point is 'main' (DXC's default, and what the RenoDX
                # entry-fix produces). Pass it explicitly to match the workflow.
                cmd = [self.tools["dxc"], '-T', dxc_prof, '-E', 'main', '-Fo', tmp, path]
            else:
                # DX11
                cmd = [self.tools["fxc"], '/nologo', '/T', prof, '/E', 'main', '/Fo', tmp, path]

            try:
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    out_paths = list(set(out_paths))
                    for op in out_paths:
                        # Never destroy the game's original blob: keep a
                        # one-time .orig backup next to it the first time a
                        # recompile lands on an existing .cso. Decompile and
                        # semantic fixup need the original as reference.
                        if os.path.exists(op) and not os.path.exists(op + ".orig"):
                            try:
                                shutil.copy2(op, op + ".orig")
                            except OSError:
                                pass
                        shutil.copy2(tmp, op)
                    self._log(f"  -> Success ({len(out_paths)} dests)")
                else:
                    self._log(f"  -> Error: {res.stderr}")
            except Exception as e: self._log(f"  -> Exception: {e}")
            if os.path.exists(tmp): os.remove(tmp)
            self.msg_queue.put(("P_STEP", None))
        self.msg_queue.put(("P_STOP", None))
        self.msg_queue.put(("REFRESH", None))

    def decompile_async(self):
        sel = self.cmp_tree.selection()
        tasks = [self.cmp_tree.item(i, "tags")[0] for i in sel if self.cmp_tree.item(i, "tags")]
        if tasks:
            self.msg_queue.put(("P_START", len(tasks)))
            threading.Thread(target=self._run_decompile, args=(tasks,)).start()

    def _run_decompile(self, tasks):
        mode = self.dx_version.get()
        for p in tasks:
            grp = os.path.relpath(os.path.dirname(p), self._get_current_dir("compiled"))
            out = os.path.join(self._get_current_dir("decompiled"), grp); os.makedirs(out, exist_ok=True)
            base = os.path.splitext(os.path.basename(p))[0]
            
            try:
                if mode == "dx11":
                    # DX11: cmd_Decompiler.exe -D <cso>
                    subprocess.run([self.tools["decompiler"], '-D', p], capture_output=True, cwd=self.dirs["compilers"])
                    
                    # Logic to move generated file (usually created next to input)
                    src_gen = os.path.join(os.path.dirname(p), base + ".hlsl")
                    if os.path.exists(src_gen):
                        shutil.move(src_gen, os.path.join(out, base + ".hlsl"))

                elif mode in ("dx12", "rdr1"):
                    method = self.decomp_method_var.get()
                    target_file = os.path.join(out, base + ".hlsl")

                    if method == "decomp":
                        # Direct decomp.exe usage
                        self._log(f"Decompiling: {base}.cso (Method: Decomp.exe)")
                        decomp_cmd = [self.tools["decomp_fallback"], p, target_file]
                        res_decomp = subprocess.run(decomp_cmd, capture_output=True, text=True, cwd=self.dirs["compilers"])

                        if res_decomp.returncode != 0:
                             self._log(f"Error (decomp.exe): {res_decomp.stderr}")
                        else:
                             self._log(f"Decompiled: {base}.hlsl")
                             entry_point = self._get_entry_point_dx12(p)
                             if entry_point and os.path.exists(target_file):
                                 try:
                                     with open(target_file, 'r') as f: content = f.read()
                                     with open(target_file, 'w') as f: 
                                         f.write(f"// Original Entry Function: {entry_point}\n// Decompiled via decomp.exe\n\n" + content)
                                 except: pass

                    else:
                        # SPIR-V Pipeline
                        # DX12: Two-step pipeline using dxil-spirv + spirv-cross
                        temp_spv = os.path.join(out, base + ".spv")
                        
                        # Detect shader type from filename for spirv-cross stage hint
                        fn_lower = base.lower()
                        shader_model = "66"  # Default SM 6.6
                        
                        # Step 1: dxil-spirv.exe - Convert DXIL to SPIR-V
                        self._log(f"Decompiling: {base}.cso (Step 1: DXIL -> SPIR-V)")
                        dxil_cmd = [
                            self.tools["dxil_spirv"], p,
                            "--output", temp_spv,
                            "--dead-code-eliminate",
                            "--use-reflection-names",
                            "--validate"
                        ]
                        
                        res1 = subprocess.run(dxil_cmd, capture_output=True, text=True, cwd=self.dirs["compilers"])
                        
                        if res1.returncode != 0 or not os.path.exists(temp_spv):
                            self._log(f"Error (dxil-spirv): {res1.stderr}")
                            self.msg_queue.put(("P_STEP", None))
                            continue
                        
                        # Step 2: spirv-cross.exe - Convert SPIR-V to HLSL
                        self._log(f"Decompiling: {base}.cso (Step 2: SPIR-V -> HLSL)")
                        
                        # Try with preferred options first
                        spirv_cmd = [
                            self.tools["spirv_cross"], temp_spv,
                            "--hlsl",
                            "--shader-model", shader_model,
                            "--hlsl-enable-16bit-types",
                            "--hlsl-preserve-structured-buffers",
                            "--relax-nan-checks",
                            "--output", target_file
                        ]
                        
                        res2 = subprocess.run(spirv_cmd, capture_output=True, text=True, cwd=self.dirs["compilers"])
                        
                        # Escalating fallback tiers, each targeting a different
                        # spirv-cross limitation. Shaders that succeed on tier 1
                        # are unaffected; later tiers only run on failure.
                        _base_opts = ["--hlsl", "--shader-model", shader_model,
                                      "--hlsl-enable-16bit-types", "--relax-nan-checks",
                                      "--output", target_file]
                        if res2.returncode != 0:
                            # Tier 2: drop --hlsl-preserve-structured-buffers
                            # (helps some complex cbuffer layouts).
                            res2 = subprocess.run([self.tools["spirv_cross"], temp_spv] + _base_opts,
                                                  capture_output=True, text=True, cwd=self.dirs["compilers"])
                        used_flatten = False
                        if res2.returncode != 0:
                            # Tier 3: --flatten-ubo rescues shaders whose $Globals
                            # holds a (statically-indexed) scalar float array that
                            # HLSL cbuffers can't 16-byte-align. READ-ONLY output:
                            # flattened uniforms merge every cbuffer into one
                            # implicit $Globals at cb0, so compiling it binds all
                            # constants wrong (silent in-game corruption).
                            res2 = subprocess.run([self.tools["spirv_cross"], temp_spv, "--flatten-ubo"] + _base_opts,
                                                  capture_output=True, text=True, cwd=self.dirs["compilers"])
                            used_flatten = (res2.returncode == 0)
                        if used_flatten and os.path.exists(target_file):
                            try:
                                with open(target_file, 'r') as f: _flat = f.read()
                                with open(target_file, 'w') as f:
                                    f.write("// READ-ONLY DECOMPILE: cbuffer layout was FLATTENED (--flatten-ubo).\n"
                                            "// Compiling this file would bind every constant buffer to cb0 and\n"
                                            "// corrupt rendering. For reference/reading only.\n"
                                            "#error This flattened decompile must not be compiled - see header.\n\n" + _flat)
                                self._log(f"  -> NOTE: {base} decompiled READ-ONLY (flattened cbuffers; do not compile).")
                            except Exception: pass

                        # Clean up temp SPIR-V file
                        if os.path.exists(temp_spv):
                            os.remove(temp_spv)

                        if res2.returncode != 0:
                            err = (res2.stderr or "").strip()
                            if "packoffset" in err or "Array stride" in err:
                                # Known spirv-cross limitation: a dynamically-indexed
                                # scalar array in a constant buffer can't be expressed
                                # as an HLSL cbuffer. Not a container/pipeline fault.
                                self._log(f"  -> Skipped {base}: constant buffer has a dynamically-indexed "
                                          f"scalar array spirv-cross can't express as HLSL (decompiler limitation).")
                            else:
                                self._log(f"Error (spirv-cross): {err}")
                        else:
                            self._log(f"Decompiled: {base}.hlsl")

                            # spirv-cross renumbers all non-SV semantics
                            # sequentially (TEXCOORD1..n), losing the original
                            # names (POSITION1, TEXCOORD0..). A recompiled
                            # stage then can't link against an original
                            # counterpart stage. Restore the original
                            # semantics from the source container.
                            try:
                                fixup_semantics(self.tools["dxc"], p, target_file, log=self._log)
                            except Exception as e:
                                self._log(f"Warning: semantic fixup failed: {e}")

                            # Add entry point info as comment
                            entry_point = self._get_entry_point_dx12(p)
                            if entry_point and os.path.exists(target_file):
                                try:
                                    with open(target_file, 'r') as f: content = f.read()
                                    with open(target_file, 'w') as f:
                                        f.write(f"// Original Entry Function: {entry_point}\n// Decompiled via dxil-spirv + spirv-cross\n\n" + content)
                                except Exception as e:
                                    self._log(f"Warning: Could not add header: {e}")

                            # Reconstruct real cbuffer parameter names from the
                            # original blob's reflection (RDR1 blobs carry full
                            # member names; the SPIR-V roundtrip discards them).
                            # Uses <cso>.orig automatically if p was recompiled.
                            try:
                                annotate_hlsl(self.tools["dxc"], p, target_file, log=self._log)
                            except Exception as e:
                                self._log(f"Warning: param-map annotation failed: {e}")
                    
            except Exception as e: 
                self._log(f"Decompile Exception: {e}")
                print(e)
            self.msg_queue.put(("P_STEP", None))
        self.msg_queue.put(("P_STOP", None))
        self.msg_queue.put(("REFRESH", None))

    def _get_entry_point_dx12(self, cso_path):
        try:
            # Run dxc -dumpbin to get ASM/Headers
            cmd = [self.tools["dxc"], '-dumpbin', cso_path]
            res = subprocess.run(cmd, capture_output=True, text=True, errors='ignore')
            if res.returncode != 0: return None
            
            # Look for "Entry function: Name"
            # Pattern might vary, assuming "Entry function: <name>" or similar
            import re
            match = re.search(r"Entry function:\s*(\w+)", res.stdout, re.IGNORECASE)
            if match: return match.group(1)
            
            # Fallback: sometimes it's just in the textual representation
            # "define void @Main(" -> Main
            match = re.search(r"define\s+\w+\s+@(\w+)\(", res.stdout)
            if match: return match.group(1)
            
        except Exception: pass
        return None

    def unpack_fxc(self):
        sel = self.fxc_tree.selection()
        tasks = [self.fxc_tree.item(i, "tags")[0] for i in sel]
        if tasks:
            self.msg_queue.put(("P_START", len(tasks)))
            threading.Thread(target=self._worker_unpack_batch, args=(tasks,)).start()

    def _worker_unpack_batch(self, tasks):
        for p in tasks:
            self._worker_unpack(p)
            self.msg_queue.put(("P_STEP", None))
        self.msg_queue.put(("P_STOP", None))
        self.msg_queue.put(("REFRESH", None))

    def _worker_unpack(self, p):
        # RDR1 rgxd .fxc: use the DX12/SM6 rdr1_fxc handler instead of the
        # DX11 FxcFile parser. Blobs land flat as NN_Name.cso under
        # compiled/rdr1/<archive>/ (+ a manifest) ready for the DX12 decompile.
        if self.dx_version.get() == "rdr1":
            try:
                folder_name = os.path.splitext(os.path.basename(p))[0]
                out = os.path.join(self.dirs["compiled"], "rdr1", folder_name)
                ents = rdr1_fxc.unpack(p, out)
                self._log(f"Unpacked (RDR1) {os.path.basename(p)} -> {len(ents)} shaders.")
            except Exception as e:
                self._log(f"Error unpacking RDR1 {os.path.basename(p)}: {e}")
            return
        try:
            with open(p, 'rb') as f: data = f.read()
            fxc = fxc_parser.FxcFile(); fxc.load(data)
            folder_name = os.path.splitext(os.path.basename(p))[0]
            # FXC Manager hardcoded to dx11 as requested
            out = os.path.join(self.dirs["compiled"], "dx11", folder_name)
            g_names = ["VS", "PS", "CS", "DS", "GS", "HS"]
            c = 0
            for i, grp in enumerate(fxc.ShaderGroups):
                if grp.Shaders:
                    gd = os.path.join(out, g_names[i]); os.makedirs(gd, exist_ok=True)
                    for sh in grp.Shaders:
                        safe = "".join([x for x in sh.Name if x.isalnum() or x in '_-']) or f"s_{c}"
                        with open(os.path.join(gd, safe+".cso"), 'wb') as fo: fo.write(sh.ByteCode)
                        c+=1
            self._log(f"Unpacked {os.path.basename(p)} -> {c} shaders.")
        except Exception as e: self._log(f"Error unpacking {p}: {e}")

    def repack_fxc(self):
        sel = self.fxc_tree.selection()
        tasks = [self.fxc_tree.item(i, "tags")[0] for i in sel]
        if tasks:
            self.msg_queue.put(("P_START", len(tasks)))
            threading.Thread(target=self._worker_repack_batch, args=(tasks,)).start()

    def _worker_repack_batch(self, tasks):
        for p in tasks:
            self._worker_repack(p)
            self.msg_queue.put(("P_STEP", None))
        self.msg_queue.put(("P_STOP", None))

    def _worker_repack(self, p):
        # RDR1 rgxd .fxc: splice edited .cso blobs back via rdr1_fxc. Size
        # changes are handled (u16 record size patched); only changed blobs are
        # swapped. Reads from compiled/rdr1/<archive>/ where unpack + the DX12
        # compile step place the .cso files.
        if self.dx_version.get() == "rdr1":
            folder_name = os.path.splitext(os.path.basename(p))[0]
            blob_dir = os.path.join(self.dirs["compiled"], "rdr1", folder_name)
            if not os.path.exists(blob_dir):
                self._log(f"Folder not found: {folder_name}. Unpack first.")
                return
            try:
                backup_dir = os.path.join(os.path.dirname(p), "backups")
                os.makedirs(backup_dir, exist_ok=True)
                shutil.copy2(p, os.path.join(backup_dir, os.path.basename(p) + ".bak"))
                rep = rdr1_fxc.repack(p, blob_dir, p, log=self._log)
                if rep > 0:
                    self._log(f"Repacked (RDR1) {os.path.basename(p)}: {rep} shader(s) updated.")
                else:
                    self._log(f"No changes for {os.path.basename(p)}.")
            except Exception as e:
                self._log(f"Error repacking RDR1 {os.path.basename(p)}: {e}")
            return
        folder_name = os.path.splitext(os.path.basename(p))[0]
        # FXC Manager hardcoded to dx11 as requested
        d_dir = os.path.join(self.dirs["compiled"], "dx11", folder_name)
        if not os.path.exists(d_dir): 
            self._log(f"Folder not found: {folder_name}. Unpack first.")
            return
        try:
            with open(p, 'rb') as f: data = f.read()
            fxc = fxc_parser.FxcFile(); fxc.load(data)
            g_names = ["VS", "PS", "CS", "DS", "GS", "HS"]; rep = 0
            for i, grp in enumerate(fxc.ShaderGroups):
                gd = os.path.join(d_dir, g_names[i])
                if os.path.exists(gd):
                    for sh in grp.Shaders:
                        safe = "".join([x for x in sh.Name if x.isalnum() or x in '_-'])
                        cp = os.path.join(gd, safe+".cso")
                        if os.path.exists(cp):
                            with open(cp, 'rb') as fb: bc = fb.read()
                            if bc != sh.ByteCode: sh.ByteCode = bc; rep+=1
            if rep > 0:
                fxc_dir = os.path.dirname(p)
                backup_dir = os.path.join(fxc_dir, "backups")
                os.makedirs(backup_dir, exist_ok=True)
                backup_path = os.path.join(backup_dir, os.path.basename(p) + ".bak")
                shutil.copy2(p, backup_path)
                with open(p, 'wb') as f: f.write(fxc.save())
                self._log(f"Repacked {os.path.basename(p)}: {rep} shaders updated.")
            else: self._log(f"No changes for {os.path.basename(p)}.")
        except Exception as e: self._log(f"Error repacking {p}: {e}")

    # ---------- AWC: Unpack / Repack (effect-grouped layout) ----------

    @staticmethod
    def _awc_safe_name(s):
        # Strip path-illegal chars. Notably exclude ':' — Windows treats it as
        # the NTFS alternate-data-stream separator, which silently hides shader
        # bytes (e.g. "postfx_lut:CS_…" becomes ADS on a file named "postfx_lut").
        return "".join(c for c in s if c.isalnum() or c in "_-.") or "unnamed"

    def _awc_build_owner_map(self, awc):
        """Return dict[(stage_key, global_idx)] -> effect_name (first owner wins)."""
        owners = {}
        stage_attrs = [
            ('vertex',   'vs_indices'), ('pixel',    'ps_indices'),
            ('geometry', 'gs_indices'), ('domain',   'ds_indices'),
            ('hull',     'hs_indices'), ('compute',  'cs_indices'),
        ]
        for eff in (getattr(awc, 'effects', None) or []):
            for stage_key, attr in stage_attrs:
                for gi in getattr(eff, attr, []) or []:
                    if (stage_key, gi) not in owners:
                        owners[(stage_key, gi)] = self._awc_safe_name(eff.name or 'unnamed')
        return owners

    def _awc_paths_for_shader(self, base_dir, archive_filename, stage_key, stage_label, gi, shader_name, owners):
        """Compute the unpacked .cso filesystem path for one shader."""
        effect_folder = owners.get((stage_key, gi), '_unassigned')
        safe = self._awc_safe_name(shader_name) or f'{stage_label}_{gi}'
        folder = os.path.join(base_dir, archive_filename, effect_folder, stage_label)
        return folder, safe + '.cso'

    def unpack_awc(self):
        sel = self.awc_file_tree.selection()
        tasks = [self.awc_file_tree.item(i, "tags")[0] for i in sel]
        if tasks:
            self.msg_queue.put(("P_START", len(tasks)))
            threading.Thread(target=self._worker_awc_unpack_batch, args=(tasks,)).start()

    def _worker_awc_unpack_batch(self, tasks):
        for p in tasks:
            self._worker_awc_unpack(p)
            self.msg_queue.put(("P_STEP", None))
        self.msg_queue.put(("P_STOP", None))
        self.msg_queue.put(("REFRESH", None))

    def _worker_awc_unpack(self, p):
        try:
            from awclib.parser import parse_awc_file
            awc = parse_awc_file(p)
            archive_filename = os.path.basename(p)
            out_base = os.path.join(self.dirs["compiled"], "dx12")
            owners = self._awc_build_owner_map(awc)
            count = 0
            stage_arrs = [
                ('vertex',   'VS', awc.vertex_shaders),
                ('pixel',    'PS', awc.pixel_shaders),
                ('geometry', 'GS', awc.geometry_shaders),
                ('domain',   'DS', awc.domain_shaders),
                ('hull',     'HS', awc.hull_shaders),
                ('compute',  'CS', awc.compute_shaders),
            ]
            for stage_key, stage_label, arr in stage_arrs:
                for gi, sh in enumerate(arr or []):
                    folder, fname = self._awc_paths_for_shader(
                        out_base, archive_filename, stage_key, stage_label, gi, sh.name, owners
                    )
                    os.makedirs(folder, exist_ok=True)
                    with open(os.path.join(folder, fname), 'wb') as fo:
                        fo.write(sh.shader_binary)
                    count += 1
            self._log(f"Unpacked {archive_filename} -> {count} shaders into {os.path.relpath(os.path.join(out_base, archive_filename), self.base_dir)}.")
        except Exception as e:
            self._log(f"Error unpacking {p}: {e}")

    def repack_awc(self):
        sel = self.awc_file_tree.selection()
        tasks = [self.awc_file_tree.item(i, "tags")[0] for i in sel]
        if tasks:
            self.msg_queue.put(("P_START", len(tasks)))
            threading.Thread(target=self._worker_awc_repack_batch, args=(tasks,)).start()

    def _worker_awc_repack_batch(self, tasks):
        for p in tasks:
            self._worker_awc_repack(p)
            self.msg_queue.put(("P_STEP", None))
        self.msg_queue.put(("P_STOP", None))
        self.msg_queue.put(("REFRESH", None))

    def _worker_awc_repack(self, p):
        try:
            from awclib.parser import parse_awc_file
            from awclib.awc_writer import rebuild_awc
            archive_filename = os.path.basename(p)
            d_dir = os.path.join(self.dirs["compiled"], "dx12", archive_filename)
            if not os.path.isdir(d_dir):
                self._log(f"Folder not found: {os.path.relpath(d_dir, self.base_dir)}. Unpack first.")
                return
            awc = parse_awc_file(p)
            owners = self._awc_build_owner_map(awc)
            replaced = 0
            stage_arrs = [
                ('vertex',   'VS', awc.vertex_shaders),
                ('pixel',    'PS', awc.pixel_shaders),
                ('geometry', 'GS', awc.geometry_shaders),
                ('domain',   'DS', awc.domain_shaders),
                ('hull',     'HS', awc.hull_shaders),
                ('compute',  'CS', awc.compute_shaders),
            ]
            for stage_key, stage_label, arr in stage_arrs:
                for gi, sh in enumerate(arr or []):
                    folder, fname = self._awc_paths_for_shader(
                        os.path.join(self.dirs["compiled"], "dx12"),
                        archive_filename, stage_key, stage_label, gi, sh.name, owners
                    )
                    fp = os.path.join(folder, fname)
                    if not os.path.exists(fp):
                        continue
                    with open(fp, 'rb') as fb:
                        bc = fb.read()
                    if bc != sh.shader_binary:
                        sh.shader_binary = bc
                        sh.size = len(bc)
                        replaced += 1
            if replaced > 0:
                awc_dir = os.path.dirname(p)
                backup_dir = os.path.join(awc_dir, "backups")
                os.makedirs(backup_dir, exist_ok=True)
                backup_path = os.path.join(backup_dir, archive_filename + ".bak")
                shutil.copy2(p, backup_path)
                rebuild_awc(awc, p, p)
                self._log(f"Repacked {archive_filename}: {replaced} shaders updated (backup at {os.path.relpath(backup_path, self.base_dir)}).")
            else:
                self._log(f"No changes for {archive_filename}.")
        except Exception as e:
            self._log(f"Error repacking {p}: {e}")

    def open_defines(self):
        DefineManagerWindow(self.root, self._get_current_dir("source"), self.refresh_source)

    def _load_hashes(self):
        if os.path.exists(self.hash_txt):
            with open(self.hash_txt, 'r') as f: c = f.read()
            for b in c.split('----------------------------------------'):
                m = re.search(r'hash\s*=\s*([^;]+);', b)
                if m and 'found in:' in b:
                    self.hash_map[m.group(1).strip()] = [x.strip() for x in re.split(r'[,\n]', b.split('found in:')[1]) if x.strip()]

    def view_asm_selected(self):
        sel = self.cmp_tree.selection()
        if not sel: return
        
        path = self.cmp_tree.item(sel[0], "tags")[0]
        if not path or not path.endswith(".cso"): return

        threading.Thread(target=self._run_asm_dump, args=(path,), daemon=True).start()

    def _run_asm_dump(self, path):
        self._log(f"Disassembling {os.path.basename(path)}...")
        try:
            cmd = []
            if self.dx_version.get() in ("dx12", "rdr1"):
                 # Try using dxc for disassembly. DXC usually supports -dumpbin on compiled shaders?
                 # Or spirv-dis equivalent?
                 # Actually `dxc.exe -dumpbin <file>` works for signed containers sometimes, or `dxc -P <file>`?
                 # Microsoft's `dxc.exe` is primarily a compiler. To disassemble a DXIL container we often need `dxil-dis` or `dxc -dumpbin`.
                 # Let's try `dxc -dumpbin`. If it fails, we fall back to generic "Not Supported" message.
                 # RDR1 blobs are DXIL containers too, so they take the dxc path.
                 cmd = [self.tools["dxc"], '-dumpbin', path]
            else:
                 # fxc.exe /nologo /dumpbin <path>
                 cmd = [self.tools["fxc"], '/nologo', '/dumpbin', path]

            res = subprocess.run(cmd, capture_output=True, text=True, errors='ignore')

            if res.returncode == 0:
                self.root.after(0, lambda: AsmWindow(self.root, os.path.basename(path), res.stdout))
                self._log("ASM view opened.")
            else:
                self._log(f"ASM Error: {res.stderr}")
                if self.dx_version.get() in ("dx12", "rdr1"):
                    self._log("Tip: Ensure 'dxc.exe' supports -dumpbin or use external tool.")
        except Exception as e:
            self._log(f"ASM Exception: {e}")

if __name__ == "__main__":
    try:
        if MODERN_UI:
            root = ttk.Window(themename="solar")
        else:
            root = tk.Tk()
        
        app = ShaderManagerApp(root)
        root.mainloop()

    except Exception as e:
        print("\n" + "="*60)
        print("!!! ПРОИЗОШЛА КРИТИЧЕСКАЯ ОШИБКА !!!")
        print("="*60 + "\n")
        traceback.print_exc()
        print("\n" + "="*60)
        input("Нажмите ENTER, чтобы выйти...")