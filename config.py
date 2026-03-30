import configparser
import os

class ConfigManager:
    def __init__(self, filename="settings.ini"):
        self.filename = filename
        self.config = configparser.ConfigParser()
        self.defaults = {
            "Paths": {
                "fxc_path": r"dxcompilers\fxc.exe",
                "decompiler_path": r"dxcompilers\cmd_Decompiler.exe",
                "dx12_compiler_path": r"dxcompilers\dxc.exe",
                "dxil_spirv_path": r"dxcompilers\dxil-spirv.exe",
                "spirv_cross_path": r"dxcompilers\spirv-cross.exe",
                "decomp_fallback_path": r"dxcompilers\decomp.exe",
                "editor_path": ""
            },
            "Window": {
                "width": "1300",
                "height": "900",
                "mode": "source",
                "theme": "solar",
                "show_welcome_banner": "true"
            }
        }

    def load(self):
        if os.path.exists(self.filename):
            self.config.read(self.filename)
        
        # Merge defaults if keys are missing
        for section, keys in self.defaults.items():
            if section not in self.config:
                self.config[section] = {}
            for k, v in keys.items():
                if k not in self.config[section]:
                    self.config[section][k] = v
        return self.config

    def save(self):
        with open(self.filename, 'w') as f:
            self.config.write(f)