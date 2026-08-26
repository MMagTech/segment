#!/usr/bin/env python3
"""Reports which init path each .mgw game takes, and so whether a tap can
ever reach it.

system.lua:494 dispatches on `type( background ) ~= 'table'`: a config
table takes the modern path, which hit-tests the libretro pointer, and
anything else is handed to compatinit, which holds no reference to
`pointer` at all. The first argument of a game's system.init call is
therefore what decides whether its drawn buttons are clickable.

Against the published set all 59 pass an image, so the modern path is
code no shipped game reaches.

Run: python3 tools/gw/classify_initpath.py [mgw-dir]
Build tools/gw/bsdump first.
"""
import os, re, subprocess, sys, tempfile
BS = 'tools/gw/bsdump'
D = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser('~/Downloads/gameandwatch')
def main_src(mgw):
    raw = subprocess.run(['bunzip2','-c',mgw],capture_output=True).stdout
    i = 0
    with tempfile.TemporaryDirectory() as td:
        while i + 512 <= len(raw):
            name = raw[i:i+100].rstrip(b'\x00 ').decode('utf-8','replace')
            if not name: break
            size = int(raw[i+124:i+136].rstrip(b'\x00 ') or b'0', 8)
            if name == 'main.bs':
                p = os.path.join(td,'main.bs'); open(p,'wb').write(raw[i+512:i+512+size])
                return subprocess.run([BS,p],capture_output=True).stdout.decode('utf-8','replace')
            i += 512 + ((size+511)//512)*512
    return ''
compat, modern, unknown = [], [], []
for fn in sorted(f for f in os.listdir(D) if f.endswith('.mgw')):
    src = main_src(os.path.join(D,fn))
    m = re.search(r'system\.init\s*\(\s*([^,\)]+)', src)
    arg = m.group(1).strip() if m else None
    has_menu = bool(re.search(r'local\s+menu\s*=', src))
    if arg is None: unknown.append((fn,'no system.init found',has_menu))
    elif arg.startswith('{') or arg == 'config' or arg.endswith('config'): modern.append((fn,arg,has_menu))
    else: compat.append((fn,arg,has_menu))
for label, rows in (('COMPAT PATH',compat),('MODERN PATH',modern),('UNCLASSIFIED',unknown)):
    print('\n=== %s: %d ===' % (label, len(rows)))
    for fn,arg,menu in rows[:70]:
        print('  %-58s %-34s menu=%s' % (fn[:58], arg[:34], menu))
