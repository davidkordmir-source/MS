"""Collect upstream notices beside the frozen application, using its build interpreter."""
from pathlib import Path
import importlib.metadata
import shutil
import sys

root = Path(__file__).resolve().parent.parent
output = root / 'dist' / 'TerrainPolygon' / 'licenses'
output.mkdir(parents=True, exist_ok=True)
shutil.copytree(root / 'packaging' / 'licenses', output / 'Qt', dirs_exist_ok=True)
for distribution in importlib.metadata.distributions():
    name = distribution.metadata['Name']
    if name.lower() in {'pip', 'setuptools', 'pyinstaller', 'pyinstaller-hooks-contrib', 'pefile', 'altgraph', 'pywin32-ctypes'}:
        continue
    for relative in distribution.files or []:
        if any(word in str(relative).lower() for word in ('license', 'copying', 'notice')):
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                target = output / name / str(relative).replace('..', '_')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
for file in (Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.base_prefix) / 'LICENSE'):
    if file.exists():
        shutil.copy2(file, output / 'Python-LICENSE.txt')
shutil.copy2(root / 'packaging' / 'THIRD_PARTY.txt', output.parent / 'THIRD_PARTY.txt')
shutil.copy2(root / 'README.md', output.parent / 'README-fa.md')
