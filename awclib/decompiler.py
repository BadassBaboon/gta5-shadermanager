"""
Shader decompiler module.
Exports shader binary from AWC and decompiles using decomp.exe for DXBC shaders.
"""

import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from .models import Shader


# Default paths to decompiler tools (can be configured)
DEFAULT_TOOLS_DIR = r"R:\CoreFXProject\_TOOLS\GTATOOLS\fxc\fxc-converter-v0.0.2\dxcompilers"


class ShaderDecompiler:
    """Handles shader binary export and decompilation."""
    
    def __init__(self, tools_dir: str = DEFAULT_TOOLS_DIR):
        self.tools_dir = Path(tools_dir)
        # decomp.exe handles DXBC (DX11-style bytecode used in GTA5E)
        self.decomp = self.tools_dir / "decomp.exe"
        
    def check_tools(self) -> Tuple[bool, str]:
        """Check if decompiler tools are available."""
        if not self.decomp.exists():
            return False, f"decomp.exe not found at: {self.decomp}"
        return True, "Tools available"
    
    def export_binary(self, shader: Shader, output_path: str) -> bool:
        """Export shader binary to a .cso file."""
        try:
            with open(output_path, 'wb') as f:
                f.write(shader.shader_binary)
            return True
        except Exception as e:
            print(f"Error exporting binary: {e}")
            return False
    
    def get_shader_format(self, shader: Shader) -> str:
        """Detect shader binary format."""
        if len(shader.shader_binary) < 4:
            return "unknown"
        magic = shader.shader_binary[:4]
        if magic == b'DXBC':
            return "dxbc"
        elif magic == b'DXIL':
            return "dxil"
        return "unknown"
    
    def get_shader_type_suffix(self, shader: Shader) -> str:
        """Determine shader type suffix from name."""
        name_lower = shader.name.lower()
        if name_lower.startswith("vs_") or name_lower.startswith("vs"):
            return "vs"
        elif name_lower.startswith("gs_"):
            return "gs"
        elif name_lower.startswith("ds_"):
            return "ds"
        elif name_lower.startswith("hs_"):
            return "hs"
        elif name_lower.startswith("cs_"):
            return "cs"
        return "ps"  # Default to pixel shader
    
    def decompile(self, shader: Shader, output_dir: str, 
                  include_cbuffer_annotations: bool = True) -> Tuple[bool, str, Optional[str]]:
        """
        Decompile a shader to HLSL.
        
        Returns: (success, message, hlsl_path or None)
        """
        ok, msg = self.check_tools()
        if not ok:
            return False, msg, None
        
        # Detect format
        fmt = self.get_shader_format(shader)
        if fmt not in ("dxbc", "dxil"):
            return False, f"Unknown shader format (expected DXBC or DXIL)", None
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create safe filename from shader name
        safe_name = shader.name.replace("\\", "_").replace("/", "_").replace(":", "_")
        safe_name = safe_name[:80]  # Limit length
        
        shader_type = self.get_shader_type_suffix(shader)
        
        # File paths
        cso_path = output_dir / f"{safe_name}.cso"
        hlsl_path = output_dir / f"{safe_name}.{shader_type}.hlsl"
        
        try:
            # Step 1: Export binary to .cso
            with open(cso_path, 'wb') as f:
                f.write(shader.shader_binary)
            
            # Step 2: Run decomp.exe
            result = subprocess.run(
                [str(self.decomp), str(cso_path), str(hlsl_path)],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                # Clean up cso file on failure
                cso_path.unlink(missing_ok=True)
                return False, f"decomp.exe failed: {result.stderr or result.stdout}", None
            
            if not hlsl_path.exists():
                cso_path.unlink(missing_ok=True)
                return False, "decomp.exe did not produce output", None
            
            # Step 3: Add header comment
            self._add_header_comment(shader, hlsl_path)
            
            # Step 4: Add cbuffer annotations if requested
            if include_cbuffer_annotations and shader.registers:
                self._add_annotations(shader, hlsl_path)
            
            # Clean up .cso file
            cso_path.unlink(missing_ok=True)
            
            return True, f"Decompiled to: {hlsl_path.name}", str(hlsl_path)
            
        except subprocess.TimeoutExpired:
            cso_path.unlink(missing_ok=True)
            return False, "Decompilation timed out", None
        except Exception as e:
            cso_path.unlink(missing_ok=True)
            return False, f"Error: {str(e)}", None
    
    def _add_header_comment(self, shader: Shader, hlsl_path: Path):
        """Add shader info header to HLSL file."""
        with open(hlsl_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        header = f"// Original Entry Function: {shader.name}\n"
        header += f"// Decompiled via decomp.exe\n"
        header += f"// Hash: 0x{shader.hash:016X}\n\n"
        
        with open(hlsl_path, 'w', encoding='utf-8') as f:
            f.write(header + content)
    
    def _add_annotations(self, shader: Shader, hlsl_path: Path):
        """Add cbuffer annotations to HLSL file."""
        from .exporter import annotate_hlsl_file
        
        # Create a single-item lookup for the exporter parser
        lookup = {
            shader.name: shader,
            shader.name.lower(): shader
        }
        
        success, msg = annotate_hlsl_file(str(hlsl_path), lookup)
        if not success:
            print(f"Annotation failed: {msg}")


def decompile_shader(shader: Shader, output_dir: str, 
                     tools_dir: str = DEFAULT_TOOLS_DIR) -> Tuple[bool, str, Optional[str]]:
    """Convenience function to decompile a shader."""
    decompiler = ShaderDecompiler(tools_dir)
    return decompiler.decompile(shader, output_dir)
