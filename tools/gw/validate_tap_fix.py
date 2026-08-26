#!/usr/bin/env python3
"""Validates the compatinit pointer fix across every shipped game.

For each .mgw: read its own source for the rectangle of its first
service button (first menu entry, or the l1 mapping for the menu-less
generation), tap the rectangle's centre in the headless bench, and
compare the whole-run output hash against an untouched run. The fix
holds for a game when the tap changes the output and the untouched run
still matches the unpatched core's behaviour (no-tap regression is
checked once, globally, not per game).

Taps land at two frames (300 and 600) because the splash swallows input
for a game-dependent stretch; a game is only called FAIL if neither
registered but a joypad press at the same frames did, i.e. the game was
provably ready and the tap still did nothing.

Run: python3 tools/gw/validate_tap_fix.py <core.dylib> <mgw-dir>
Needs tools/gw/bsdump and tools/bench/libretro_bench built.
"""
import os, re, subprocess, sys, tempfile

BENCH = 'tools/bench/libretro_bench'
BS = 'tools/gw/bsdump'
FRAMES = 900
TAPS = (300, 600)

def sources(mgw):
    raw = subprocess.run(['bunzip2', '-c', mgw], capture_output=True).stdout
    out, i = {}, 0
    with tempfile.TemporaryDirectory() as td:
        while i + 512 <= len(raw):
            name = raw[i:i+100].rstrip(b'\x00 ').decode('utf-8', 'replace')
            if not name: break
            size = int(raw[i+124:i+136].rstrip(b'\x00 ') or b'0', 8)
            if name in ('main.bs', 'unit1.bs'):
                p = os.path.join(td, name)
                open(p, 'wb').write(raw[i+512:i+512+size])
                out[name] = subprocess.run([BS, p], capture_output=True).stdout.decode('utf-8', 'replace')
            i += 512 + ((size + 511) // 512) * 512
    return out

def geometry(unit, control):
    geo = {}
    for f in ('left', 'top', 'width', 'height'):
        m = re.search(r'self\.%s\.%s = (\d+)' % (re.escape(control), f), unit)
        if m: geo[f] = int(m.group(1))
    return [geo['left'], geo['top'], geo['width'], geo['height']] if len(geo) == 4 else None

def target_rect(main, unit):
    """Centre of the first service button the game declares."""
    m = re.search(r'local\s+menu\s*=\s*\{.*?\{\s*unit1\.form1\.(\w+)\s*,\s*"([^"]+)"', main, re.S)
    if m:
        rect = geometry(unit, m.group(1).replace('_down', '_top'))
        if rect: return rect, m.group(2)
    km = re.search(r'l1\s*=\s*\{\s*\d+\s*,\s*"([^"]+)"', main)
    if km:
        label = km.group(1)
        rect = (geometry(unit, 'btn_' + label.lower().replace(' ', '_') + '_top')
                or geometry(unit, 'btn_mode_top'))
        if rect: return rect, label
    return None, None

def canvas(unit):
    # The Multi Screen clamshells declare im_background_open instead
    for bg in ('im_background', 'im_background_open'):
        m = re.search(r'self\.%s\.width = (\d+)' % bg, unit)
        n = re.search(r'self\.%s\.height = (\d+)' % bg, unit)
        if m and n: return int(m.group(1)), int(n.group(1))
    return None, None

def out_hash(core, mgw, args):
    cmd = [BENCH, core, mgw, '-f', str(FRAMES), '-s', '/private/tmp/gwsys'] + args
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r'"output_hash": "(\d+)"', r.stdout)
    return m.group(1) if m else None

def main():
    core, mgwdir = sys.argv[1], sys.argv[2]
    ok = fail = skip = 0
    for fn in sorted(f for f in os.listdir(mgwdir) if f.endswith('.mgw')):
        mgw = os.path.join(mgwdir, fn)
        srcs = sources(mgw)
        rect, label = target_rect(srcs.get('main.bs', ''), srcs.get('unit1.bs', ''))
        w, h = canvas(srcs.get('unit1.bs', ''))
        if not rect or not w:
            skip += 1
            print('%-52s SKIP no rect/canvas' % fn[:52]); continue
        cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        px = round(cx * 65534 / w) - 32767
        py = round(cy * 65534 / h) - 32767
        tap_args = sum((['-tap', '%d,%d,%d,30,2' % (f, px, py)] for f in TAPS), [])
        # Clamshells boot closed with their buttons hidden; a click on the
        # closed lid opens them. Tap the lid's centre before the button.
        m = re.search(r'self\.im_background_closed\.width = (\d+)', srcs.get('unit1.bs', ''))
        n = re.search(r'self\.im_background_closed\.height = (\d+)', srcs.get('unit1.bs', ''))
        if m and n:
            ox = round(int(m.group(1)) // 2 * 65534 / w) - 32767
            oy = round(int(n.group(1)) // 2 * 65534 / h) - 32767
            tap_args = ['-tap', '%d,%d,%d,30,2' % (TAPS[0] - 60, ox, oy)] + tap_args
        base = out_hash(core, mgw, [])
        taps = out_hash(core, mgw, tap_args)
        if taps != base:
            ok += 1
            print('%-52s OK   tap on %-8s changed output' % (fn[:52], label))
            continue
        pad = out_hash(core, mgw, sum((['-i', '%d,2,30' % f] for f in TAPS), []))
        if pad == base:
            skip += 1
            print('%-52s SKIP not ready by frame 600 (joypad dead too)' % fn[:52])
        else:
            fail += 1
            print('%-52s FAIL joypad works, tap on %s does not' % (fn[:52], label))
    print('\n%d ok, %d fail, %d skip' % (ok, fail, skip))

if __name__ == '__main__': main()
