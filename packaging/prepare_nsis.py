"""Generate exact uninstall paths; preserve user-created files during removal."""
from pathlib import Path
root = Path(__file__).resolve().parent.parent
bundle = root / 'dist' / 'TerrainPolygon'
if not (bundle / 'TerrainPolygon.exe').is_file():
    raise RuntimeError('Build the Windows application before preparing the installer')
(root / 'dist' / 'installer').mkdir(parents=True, exist_ok=True)
def escaped(path):
    return str(path.relative_to(bundle)).replace('/', '\\').replace('$', '$$').replace('"', '$\\"')
lines = [f'Delete "$INSTDIR\\{escaped(p)}"' for p in sorted(bundle.rglob('*')) if p.is_file()]
dirs = sorted((p for p in bundle.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
lines.extend(f'RMDir "$INSTDIR\\{escaped(p)}"' for p in dirs)
(root / 'packaging' / 'uninstall-files.nsh').write_text('\n'.join(lines) + '\n', encoding='utf-8')
