"""Build the local menu-bar shortcut; requires Apple's command-line tools."""
import plistlib
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parent.parent
bundle=ROOT/'dist/Sentinel Menu.app'
(bundle/'Contents/MacOS').mkdir(parents=True,exist_ok=True)
(bundle/'Contents/Resources').mkdir(parents=True,exist_ok=True)
(bundle/'Contents/Resources/project-root.txt').write_text(str(ROOT))
with (bundle/'Contents/Info.plist').open('wb') as handle:
    plistlib.dump({'CFBundleName':'Sentinel Menu','CFBundleIdentifier':'local.sentinel.menubar',
                  'CFBundleExecutable':'SentinelMenu','CFBundlePackageType':'APPL',
                  'CFBundleVersion':'1','LSUIElement':True,'NSHighResolutionCapable':True},handle)
subprocess.run(['/usr/bin/swiftc',str(ROOT/'desktop/MenuBar.swift'),'-o',str(bundle/'Contents/MacOS/SentinelMenu'),'-framework','AppKit','-module-cache-path',str(ROOT/'dist/module-cache')],check=True)
print(bundle)
