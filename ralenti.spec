# PyInstaller spec, one-folder build. Bundles the optional ralenti_ext package when present.
import os
from PyInstaller.utils.hooks import collect_data_files

ext_dir = os.path.join(SPECPATH, "ralenti_ext")
ext = ["ralenti_ext"] + [f"ralenti_ext.{f[:-3]}" for f in os.listdir(ext_dir)
                         if f.endswith(".py") and f != "__init__.py"] if os.path.isdir(ext_dir) else []

a = Analysis(
    ["run_ralenti.py"],
    pathex=["."],
    datas=[("ralenti/resources", "ralenti/resources")] + collect_data_files("qtawesome"),
    hiddenimports=["PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSvg",
                   "qtawesome", "qtpy", "psutil"] + ext,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "pytest",
              "PyQt5", "PyQt6", "PySide2"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Ralenti", console=False,
          icon="ralenti/resources/ralenti.ico", upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Ralenti", upx=False)
