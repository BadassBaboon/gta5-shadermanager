import os
import re
import tkinter as tk
from tkinter import LEFT, RIGHT, BOTH, X, Y, END

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
except ImportError:
    import tkinter.ttk as ttk

class DefineManagerWindow(ttk.Toplevel):
    def __init__(self, parent, source_dir, on_close_callback):
        super().__init__(parent)
        self.title("Global Defines")
        self.geometry("800x600")
        
        self.source_dir = source_dir
        self.on_close_callback = on_close_callback
        self.defines_map = {}
        
        self._create_ui()
        self._scan_defines()

    def _create_ui(self):
        ttk.Label(self, text="⚙ Global Parameter Manager", font=("Segoe UI", 16, "bold")).pack(pady=15)
        
        filter_fr = ttk.Frame(self, padding=10)
        filter_fr.pack(fill=X)
        
        self.search_var = tk.StringVar()
        self.search_var.trace("w", self._filter_list)
        ttk.Entry(filter_fr, textvariable=self.search_var, bootstyle="primary", width=40).pack(side=LEFT, padx=5)
        ttk.Button(filter_fr, text="Rescan Files", command=self._scan_defines, bootstyle="outline-secondary").pack(side=RIGHT)
        
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=BOTH, expand=True, padx=15, pady=5)

        self.tree = ttk.Treeview(tree_frame, columns=("name", "value", "count"), show="headings", bootstyle="info")
        self.tree.heading("name", text="Parameter"); self.tree.column("name", width=350)
        self.tree.heading("value", text="Value"); self.tree.column("value", width=150)
        self.tree.heading("count", text="Files"); self.tree.column("count", width=80, anchor="center")
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        sb.pack(side=RIGHT, fill=Y)
        self.tree.configure(yscrollcommand=sb.set)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        
        edit_fr = ttk.Frame(self, padding=15)
        edit_fr.pack(fill=X, side=BOTTOM)
        
        ttk.Label(edit_fr, text="New Value:").pack(side=LEFT)
        self.val_entry = ttk.Entry(edit_fr, bootstyle="success")
        self.val_entry.pack(side=LEFT, fill=X, expand=True, padx=10)
        
        self.name_ref = ttk.Entry(edit_fr, width=30, state="readonly")
        self.name_ref.pack(side=LEFT)
        
        ttk.Button(edit_fr, text="Apply to ALL Files", command=self._apply_changes, bootstyle="success").pack(side=LEFT, padx=10)

    def _scan_defines(self):
        self.defines_map.clear()
        # Regex handles: #define NAME Value //comment
        regex = re.compile(r'^(\s*)#define\s+([A-Za-z_][A-Za-z0-9_]*)(?!\()\s+(.+?)(?:\s*//.*)?$')
        
        for root, _, files in os.walk(self.source_dir):
            for file in files:
                if file.endswith(".hlsl"):
                    path = os.path.join(root, file)
                    try:
                        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                            for line in f:
                                match = regex.match(line)
                                if match:
                                    name = match.group(2)
                                    val = match.group(3).strip()
                                    
                                    if name not in self.defines_map: 
                                        self.defines_map[name] = {"value": val, "files": set()}
                                    
                                    # Mark as mixed if values differ
                                    if self.defines_map[name]["value"] != val: 
                                        self.defines_map[name]["value"] = "*MIXED*"
                                    
                                    self.defines_map[name]["files"].add(path)
                    except: pass
        self._populate_tree()

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        search = self.search_var.get().lower()
        
        for name, data in sorted(self.defines_map.items()):
            if search and search not in name.lower(): 
                continue
            self.tree.insert("", "end", values=(name, data["value"], len(data["files"])))

    def _filter_list(self, *args): 
        self._populate_tree()

    def _on_select(self, event):
        sel = self.tree.selection()
        if not sel: return
        n, v, _ = self.tree.item(sel[0])['values']
        
        self.name_ref.config(state="normal")
        self.name_ref.delete(0, END)
        self.name_ref.insert(0, n)
        self.name_ref.config(state="readonly")
        
        self.val_entry.delete(0, END)
        if v != "*MIXED*": 
            self.val_entry.insert(0, v)

    def _apply_changes(self):
        name = self.name_ref.get()
        new_val = self.val_entry.get()
        if not name: return
        
        # Regex to find exact line to replace
        regex = re.compile(r'^(\s*#define\s+' + re.escape(name) + r'\s+)(.+?)((\s*//.*)?)$')
        
        for path in self.defines_map[name]["files"]:
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f: 
                    lines = f.readlines()
                
                new_lines = []
                for line in lines:
                    m = regex.match(line)
                    if m:
                        # Reconstruct: Indent + #define Name + NewValue + Comment
                        new_lines.append(f"{m.group(1)}{new_val}{m.group(3).rstrip()}\n")
                    else:
                        new_lines.append(line)
                
                with open(path, 'w', encoding='utf-8') as f: 
                    f.writelines(new_lines)
            except: pass
        
        self._scan_defines()
        if self.on_close_callback: 
            self.on_close_callback()