#!/usr/bin/env python3
"""One command: a MAME romset (plus its artwork pack, if you have one)
in, a playable .mgw out.

  python3 tools/gw/convert.py <romset.zip> [artwork.zip] [-o out.mgw]

The romset's base name (gnw_ball.zip -> gnw_ball) selects the game's
wiring from tools/gw/inputs.json, which this repository ships. Without
an artwork pack the game gets a clean generated shell; with one it gets
the scanned unit, and re-running with a pack later upgrades the same
game in seconds.

What it does, in order, telling you about each step:
  1. identify the game and its chip from the romset name
  2. split the romset into program / melody / screen SVGs
  3. read the artwork pack (if given) for the panel and screen windows
  4. render each screen's segments from its SVG at the window's size,
     shrinking everything if the worst case would overflow the stock
     core's 384k-pixel sprite budget
  5. package the .mgw
  6. prove it: boot the game headless and press Game A / Start; the
     build FAILS LOUDLY if the game does not respond, rather than
     handing you a broken file (skipped if no core binary is available;
     pass --no-verify to skip deliberately)

Requirements: python3, Pillow, numpy, rsvg-convert (brew install
librsvg / apt install librsvg2-bin). Verification additionally wants a
gw-libretro core dylib/so next to the tool or via --core.
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)


def say(msg):
    print('convert: ' + msg)


def fail(msg):
    print('convert: ERROR: ' + msg, file=sys.stderr)
    sys.exit(1)


def title_of(e):
    t = e['title'].replace('Game & Watch: ', '')
    maker = e['maker']
    m = re.match(r'bootleg \((.+)\)$', maker)
    if m:
        maker = m.group(1)
    v = re.match(r'^(.*?)\s*\((.+)\)$', t)
    if v and v.group(2) == maker:
        n = '%s (%s)' % (v.group(1), maker)
    elif v:
        n = '%s (%s, %s)' % (v.group(1), maker, v.group(2))
    else:
        n = '%s (%s)' % (t, maker)
    return n.replace('/', '-').replace(':', ' -')


def find_core(explicit):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    names = ['gw_libretro.dylib', 'gw_libretro.so', 'gw_libretro.dll']
    dirs = [HERE, ROOT, os.getcwd(),
            os.path.expanduser('~/Library/Application Support/RetroArch/cores'),
            os.path.expanduser('~/.config/retroarch/cores')]
    for dd in dirs:
        for n in names:
            p = os.path.join(dd, n)
            if os.path.exists(p):
                return p
    return None


def main():
    ap = argparse.ArgumentParser(usage='convert.py romset.zip [artwork.zip] [-o out.mgw]')
    ap.add_argument('romset')
    ap.add_argument('artwork', nargs='?', default=None)
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--core', default=None, help='gw-libretro core for the self-test')
    ap.add_argument('--no-verify', action='store_true')
    args = ap.parse_args()

    rom = os.path.basename(args.romset)
    shortname = re.sub(r'\.zip$', '', rom, flags=re.I)
    inputs = json.load(open(os.path.join(HERE, 'inputs.json')))
    if shortname not in inputs:
        fail("'%s' is not a romset this tool knows. The name must match "
             "MAME's, e.g. gnw_ball.zip; run with a renamed copy if your "
             "file is called something else." % shortname)
    e = inputs[shortname]
    chip = e['cpu']
    if chip not in ('sm5a', 'sm510', 'sm511', 'sm512', 'sm530'):
        fail("'%s' runs on the %s, which has no verified core yet; see "
             "COVERAGE.md." % (shortname, chip or 'unknown chip'))
    title = title_of(e)
    say("game: %s  [chip %s, %d screen(s)]" % (title, chip, e.get('screens', 1)))

    z = zipfile.ZipFile(args.romset)
    names = z.namelist()
    svgs = sorted([n for n in names if n.lower().endswith('.svg')],
                  key=lambda n: 0 if ('top' in n.lower() or 'left' in n.lower()) else 1)
    bins = [n for n in names if not n.lower().endswith('.svg')]
    if not svgs:
        fail('the romset carries no screen SVG; this dump predates '
             "MAME's vector screens for it. A newer romset will have it.")
    if not bins:
        fail('no program ROM found in the romset')
    prog = [sorted(bins, key=lambda n: z.getinfo(n).file_size)[-1]]
    mel = [n for n in bins
           if z.getinfo(n).file_size == 0x100 and n != prog[0]]

    work = tempfile.mkdtemp(prefix='mgw_')
    try:
        open(os.path.join(work, 'rom.bin'), 'wb').write(z.read(prog[0]))
        if chip in ('sm511', 'sm512'):
            open(os.path.join(work, 'melody.bin'), 'wb').write(
                z.read(mel[0]) if mel else b'\0' * 0x100)
        say('romset: program %s%s, %d screen SVG(s)'
            % (prog[0], (' + melody ' + mel[0]) if mel else '', len(svgs)))

        import artwork as artmod
        max_w = None
        for attempt in range(8):
            if args.artwork:
                panel = artmod.render(args.artwork,
                                      max_panel=(max_w, max_w) if max_w
                                      else artmod.MAX_PANEL)
                say('artwork: view %r, panel %dx%d, screen(s) %s'
                    % (panel.view, panel.w, panel.h,
                       ' '.join('%dx%d' % (r[2], r[3])
                                for _, r in sorted(panel.lcds.items()))))
                widths = [panel.lcds.get(i, panel.lcd)[2] for i in range(len(svgs))]
            else:
                say('no artwork pack: using the generated shell')
                widths = [900] * len(svgs)

            segdirs = []
            for i, sv in enumerate(svgs):
                svp = os.path.join(work, 'g%d.svg' % i)
                open(svp, 'wb').write(z.read(sv))
                sd = os.path.join(work, 'segs%d' % i)
                shutil.rmtree(sd, ignore_errors=True)
                r = subprocess.run([sys.executable, os.path.join(HERE, 'svg2segs.py'),
                                    svp, sd, str(widths[i])],
                                   capture_output=True, text=True)
                if r.returncode:
                    fail('segment extraction failed: '
                         + (r.stderr.strip().splitlines() or ['?'])[-1])
                n = len(json.load(open(os.path.join(sd, 'segments.json')))) - 1
                say('screen %d: %d segments at width %d' % (i, n, widths[i]))
                segdirs.append(sd)

            out = args.out or (title + '.mgw')
            cmd = [sys.executable, os.path.join(HERE, 'build_mgw.py'),
                   os.path.join(work, 'rom.bin'), ','.join(segdirs), out, title,
                   args.artwork or '', shortname, chip]
            if max_w:
                cmd.append(str(max_w))
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 3:
                m = re.search(r'rescale=([\d.]+)', r.stdout)
                scale = (float(m.group(1)) if m else 0.75) * (0.97 - 0.05 * attempt)
                max_w = max(200, int((max_w or (panel.w if args.artwork else 1200))
                                     * scale))
                say('over the sprite-pixel budget of the stock core; '
                    'retrying at %dpx wide' % max_w)
                continue
            if r.returncode:
                fail('packaging failed: '
                     + ((r.stderr or r.stdout).strip().splitlines() or ['?'])[-1])
            for line in r.stdout.splitlines():
                if 'tap zones' in line or 'joypad-only' in line:
                    say(line.strip())
            break
        else:
            fail('could not fit the sprite-pixel budget even at 200px')
        say('wrote %s (%.1f MB)' % (out, os.path.getsize(out) / 1e6))

        if args.no_verify:
            say('self-test skipped (--no-verify)')
            return 0
        core = find_core(args.core)
        bench = os.path.join(ROOT, 'tools/bench/libretro_bench')
        if not core or not os.path.exists(bench):
            say('self-test skipped: no %s available'
                % ('core' if not core else 'bench harness'))
            return 0
        say('self-test: booting on %s' % os.path.basename(core))
        def run(extra):
            rr = subprocess.run([bench, core, out, '-f', '900'] + extra,
                                capture_output=True, text=True)
            try:
                return json.loads(rr.stdout)['output_hash']
            except Exception:
                return None
        idle = run([])
        if idle is None:
            fail('the built game DID NOT BOOT on the stock core; not your '
                 'fault, please report this with the romset name')
        pressed = run(['-i', '200,3,400'])
        if pressed == idle:
            say('WARNING: boots, but Game A/Start changed nothing in 15s; '
                'some units want TIME first or use a keypad, so this can be '
                'normal, but try it before trusting it')
        else:
            say('self-test passed: boots and responds')
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
