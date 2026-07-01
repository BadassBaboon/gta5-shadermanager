"""
Tooltip module for the GTA 5 Shader Manager.
Provides hover-based tooltips for any tkinter widget.
"""
import tkinter as tk

class ToolTip:
    """
    Displays a styled tooltip when hovering over a widget.
    
    Usage:
        ToolTip(some_button, "This button compiles the selected shader files.")
    """
    
    DELAY_MS = 450       # Delay before tooltip appears
    WRAP_LENGTH = 320    # Max width before wrapping text
    
    # Solarized-inspired colors
    BG = "#073642"
    FG = "#93a1a1"
    BORDER = "#586e75"
    
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self._after_id = None
        
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")
    
    def _on_enter(self, event=None):
        self._cancel()
        self._after_id = self.widget.after(self.DELAY_MS, self._show)
    
    def _on_leave(self, event=None):
        self._cancel()
        self._hide()
    
    def _cancel(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
    
    def _show(self):
        if self.tip_window or not self.text:
            return
        
        # Position tooltip below the widget
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        
        # Ensure tooltip stays on top
        try:
            tw.wm_attributes("-topmost", True)
        except Exception:
            pass
        
        # Outer frame for border effect
        frame = tk.Frame(tw, background=self.BORDER, bd=0)
        frame.pack()
        
        label = tk.Label(
            frame,
            text=self.text,
            justify=tk.LEFT,
            background=self.BG,
            foreground=self.FG,
            relief=tk.FLAT,
            borderwidth=0,
            wraplength=self.WRAP_LENGTH,
            font=("Segoe UI", 9),
            padx=8,
            pady=5
        )
        label.pack(padx=1, pady=1)
    
    def _hide(self):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


def hint(widget, text):
    """Convenience function: attach a tooltip to any widget."""
    return ToolTip(widget, text)
