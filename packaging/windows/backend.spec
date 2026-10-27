# -*- mode: python ; coding: utf-8 -*-
import json
import os
from pathlib import Path
import sys

config = json.loads(Path(os.environ["AMADEUS_BUILD_CONFIG"]).read_text(encoding="utf-8"))
sys.path.insert(0, config["packaging"])
from bundle_support import collect_inputs, record_analysis

datas, binaries, hiddenimports, excludes = collect_inputs(config)
a = Analysis(
    [str(Path(config["packaging"]) / "backend_entry.py")],
    pathex=[str(Path(config["repo"]) / "backend")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    excludes=excludes, noarchive=False,
)
record_analysis(a, config)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="amadeus-backend", debug=False, strip=False, upx=False, console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="amadeus-backend")
