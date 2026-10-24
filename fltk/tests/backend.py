#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import os,subprocess,sys
p=subprocess.run([sys.argv[1],'--help'],text=True,capture_output=True,check=True)
assert 'Wayland' in p.stdout
p=subprocess.run([sys.argv[1],'--x11'],env={**os.environ,'DISPLAY':':9999'},text=True,capture_output=True)
assert p.returncode!=0 and 'build-time opt-in' in p.stderr
print('Default Wayland frontend rejects unconfigured X11 before opening a display')
