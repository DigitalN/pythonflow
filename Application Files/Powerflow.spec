# PyInstaller spec for Powerflow
# Builds a menu-bar-only app (LSUIElement): no Dock icon until the dashboard is opened.
# Data lives in ~/Library/Application Support/Powerflow, logs in ~/Library/Logs/Powerflow.

import os

ICON_PATH = os.path.join(SPECPATH, 'Powerflow.icns')
VERSION = '1.0.0'

a = Analysis(
    ['main.py'],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        ('index.html', '.'),
    ],
    hiddenimports=[
        'webview',
        'webview.platforms.cocoa',
        'objc',
        'Foundation',
        'AppKit',
        'WebKit',
        'PyObjCTools.AppHelper',
    ],
    hookspath=[],
    runtime_hooks=[],
    # Other pywebview backends and unused stdlib GUI toolkits.
    excludes=[
        'tkinter',
        'webview.platforms.android',
        'webview.platforms.cef',
        'webview.platforms.edgechromium',
        'webview.platforms.gtk',
        'webview.platforms.mshtml',
        'webview.platforms.qt',
        'webview.platforms.winforms',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Powerflow',
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Powerflow',
)

app = BUNDLE(
    coll,
    name='Powerflow.app',
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
    bundle_identifier='com.powerflow.monitor',
    info_plist={
        'CFBundleName': 'Powerflow',
        'CFBundleDisplayName': 'Powerflow',
        'CFBundleVersion': VERSION,
        'CFBundleShortVersionString': VERSION,
        'LSUIElement': True,
        'LSMinimumSystemVersion': '12.0',
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'MIT License. Based on Powerflow by The Powerflow Team.',
    },
)
