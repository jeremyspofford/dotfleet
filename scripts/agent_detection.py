"""Find coding agents from commands, installed Mise packages, or configuration folders."""
import json
from pathlib import Path
import shutil
import subprocess


def inventory(home):
    if not shutil.which('mise'):
        return {}
    result = subprocess.run(['mise', 'ls', '--installed', '--json', '--cd', str(home)],
                            text=True, capture_output=True)
    return json.loads(result.stdout) if result.returncode == 0 else {}


def detected(home, manifest, tools=None):
    tools = inventory(home) if tools is None else tools
    installed = {name.removeprefix('npm:') for name, versions in tools.items() if versions}
    result = {}
    for name, spec in manifest.items():
        packages = set(spec.get('mise_packages', [spec['command']]))
        folders = {str(Path(path).parent) for path in spec.get('config_paths', {}).values() if path}
        folders.update(spec.get('config_folders', []))
        if shutil.which(spec['command']) or packages & installed or any((home / path).is_dir() for path in folders):
            result[name] = spec
    return result
