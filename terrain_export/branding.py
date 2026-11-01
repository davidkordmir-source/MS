from pathlib import Path
import sys
from PySide6.QtGui import QIcon

VERSION = '0.5.0'
WINDOWS_APP_ID = 'TerrainStudio.Desktop'


def asset_path(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))/'assets'/name


def app_icon():
    ico = asset_path('terrain-studio.ico')
    return QIcon(str(ico if ico.exists() else asset_path('terrain-studio.png')))
