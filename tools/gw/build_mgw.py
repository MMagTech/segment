#!/usr/bin/env python3
"""Packages a MAME SM5A handheld as a self-contained .mgw for gw-libretro.

Takes the ROM and the segment bitmaps produced by svg2segs.py, draws a
panel around the LCD, and emits a .mgw (bzip2'd tar) containing:

  main.bs       tiny bs-encoded stub: return system.loadunit 'game'
  game.lua      the machine: panel, segments, input map, frame tick
  sm5a.lua      the CPU core (shared, unmodified)
  rom.bin       the chip ROM
  *.rle         panel background and one image per segment

The game code drives the modern gw runtime directly (setbackground,
newimage, inputstate), so it needs neither compatinit nor the hand
menu: buttons are tappable via its own hit rectangles, matching the
pointer support the core already has.

Run: python3 tools/gw/build_mgw.py <rom> <segdir> <out.mgw> <title>
"""
import io, json, os, struct, subprocess, sys, tarfile, bz2, zlib, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svg2segs import read_png
import artwork

# ---------------------------------------------------------------- rle encoder

def rgb565(r, g, b):
    return (r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)

def encode_rle(w, h, rgba, bgcolor=None):
    """retroluxury rl_image: BE u16 w,h; BE u32 used; BE u32 row byte
    offsets into the stream; rows of [u16 1][u16 nruns][runs].
    Alpha quantizes to the five run types the blitter knows."""
    def classify(a):
        if a < 32: return 0
        if a < 96: return 1      # 25%
        if a < 160: return 2     # 50%
        if a < 224: return 3     # 75%
        return 4                 # opaque
    rows, used = [], 0
    for y in range(h):
        runs = []
        x = 0
        while x < w:
            i = (y * w + x) * 4
            t = classify(rgba[i + 3])
            x1 = x
            while x1 < w and classify(rgba[(y * w + x1) * 4 + 3]) == t:
                x1 += 1
            count = x1 - x
            if t == 0:
                runs.append((0, count, b''))
            else:
                px = bytearray()
                for xx in range(x, x1):
                    j = (y * w + xx) * 4
                    px += struct.pack('>H', rgb565(rgba[j], rgba[j+1], rgba[j+2]))
                runs.append((t, count, bytes(px)))
                used += count
            x = x1
        rows.append(runs)
    stream = io.BytesIO()
    offsets = []
    for runs in rows:
        offsets.append(stream.tell())
        stream.write(struct.pack('>HH', 1, len(runs)))
        for t, count, px in runs:
            stream.write(struct.pack('>H', t << 13 | count))
            stream.write(px)
    out = io.BytesIO()
    out.write(struct.pack('>HH', w, h))
    out.write(struct.pack('>I', used))
    for o in offsets: out.write(struct.pack('>I', o))
    out.write(stream.getvalue())
    return out.getvalue()

# ---------------------------------------------------------------- panel

def panel_layout(wiring, lw, lh):
    """Derive the drawn panel's geometry from what the game's wiring
    says exists: paired side buttons, a d-pad, action buttons, and a
    pill row. Returns the layout dict main() expects, with every drawn
    control doubling as a tap zone."""
    acts = set()
    for col in wiring['columns']:
        acts |= set(col)
    acts |= {p['action'] for p in wiring.get('pins', {}).values()}

    def side(kinds):
        return [k for k in kinds if k in acts]

    left = side(('lup', 'ldown')) or side(('left',))
    right = side(('rup', 'rdown')) or side(('right',))
    dpad = [k for k in ('up', 'down', 'left', 'right') if k in acts]
    use_dpad = len(dpad) >= 3
    if use_dpad:
        left = []
    fire = [k for k in ('b1', 'b2', 'b3', 'jump', 'hit', 'fire', 'shoot',
                        'punch', 'button4') if k in acts][:2]
    if not right and fire:
        right, fire = fire, []
    pills = [k for k in ('gamea', 'gameb', 'time', 'alarm', 'start',
                         'pause', 'sound', 'select') if k in acts][:5]

    BTN_R = 34                      # big round button radius
    side_w = 118 if (left or right or use_dpad or fire) else 28
    title_h = 64
    pill_h = 74 if pills else 20
    W = max(560, lw + 2 * side_w)
    lx = (W - lw) // 2
    ly = title_h
    H = title_h + lh + pill_h + 26

    buttons = []
    def round_btn(cx, cy, act, label, r=BTN_R):
        buttons.append({'shape': 'round', 'r': r, 'cx': cx, 'cy': cy,
                        'act': act, 'label': label,
                        'rect': [cx - r, cy - r, 2 * r, 2 * r]})
    LABEL = {'lup': 'UP', 'ldown': 'DOWN', 'rup': 'UP', 'rdown': 'DOWN',
             'left': 'LEFT', 'right': 'RIGHT', 'b1': 'A', 'b2': 'B',
             'b3': 'C', 'jump': 'JUMP', 'hit': 'HIT', 'fire': 'FIRE',
             'shoot': 'FIRE', 'punch': 'PUNCH', 'button4': 'D',
             'gamea': 'GAME A', 'gameb': 'GAME B', 'time': 'TIME',
             'alarm': 'ALARM', 'start': 'START', 'pause': 'PAUSE',
             'sound': 'SOUND', 'select': 'SELECT', 'up': 'UP',
             'down': 'DOWN'}

    cy_mid = ly + lh // 2
    def stack(cluster, cx):
        if len(cluster) == 2:
            round_btn(cx, cy_mid - 52, cluster[0], LABEL[cluster[0]], 30)
            round_btn(cx, cy_mid + 52, cluster[1], LABEL[cluster[1]], 30)
        elif cluster:
            round_btn(cx, cy_mid, cluster[0], LABEL[cluster[0]])
    if use_dpad:
        cx = side_w // 2
        for act, dx, dy in (('up', 0, -40), ('down', 0, 40),
                            ('left', -40, 0), ('right', 40, 0)):
            if act in dpad:
                round_btn(cx + dx, cy_mid + dy, act, '', 22)
    else:
        stack(left, side_w // 2)
    stack(right if not use_dpad else (right or fire), W - side_w // 2)
    if use_dpad and right and fire:
        stack(fire, W - side_w // 2 - 0)   # fire shares the right stack

    if pills:
        pw, gap = 74, 26
        total = len(pills) * pw + (len(pills) - 1) * gap
        x = (W - total) // 2
        py = ly + lh + 22
        for a in pills:
            buttons.append({'shape': 'pill', 'act': a, 'label': LABEL[a],
                            'rect': [x, py, pw, 24]})
            x += pw + gap

    return {'panel_w': W, 'panel_h': H, 'lcd_x': lx, 'lcd_y': ly,
            'lcd_w': lw, 'lcd_h': lh, 'artwork': False,
            'lcds': [(lx, ly, lw, lh)], 'buttons': buttons}


def build_panel(segdir, layout, title='LCD'):
    """Render the generated panel with rsvg: a clean modern shell, dark
    bezel around the LCD, convex round action buttons, labelled pills.
    Returns (w, h, rgba)."""
    import base64
    bd = open(os.path.join(segdir, 'backdrop.png'), 'rb').read()
    b64 = base64.b64encode(bd).decode()
    W, H = layout['panel_w'], layout['panel_h']
    lx, ly = layout['lcd_x'], layout['lcd_y']
    lw, lh = layout['lcd_w'], layout['lcd_h']
    title_upper = title.upper()[:26]
    parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
  viewBox="0 0 {W} {H}">
  <defs>
    <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#f2f0ec"/>
      <stop offset="1" stop-color="#dcdad4"/>
    </linearGradient>
    <linearGradient id="btn" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#e6473c"/>
      <stop offset="1" stop-color="#b02a22"/>
    </linearGradient>
    <linearGradient id="bez" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1d1d22"/>
      <stop offset="1" stop-color="#34343c"/>
    </linearGradient>
  </defs>
  <rect width="{W}" height="{H}" rx="26" fill="url(#body)"/>
  <rect x="1.5" y="1.5" width="{W-3}" height="{H-3}" rx="25"
    fill="none" stroke="#b9b6ae" stroke-width="3"/>
  <text x="{lx}" y="42" font-family="Helvetica Neue, Helvetica, Arial"
    font-size="24" font-weight="bold" fill="#3a3a40"
    letter-spacing="5">{title_upper}</text>
  <rect x="{lx-14}" y="{ly-14}" width="{lw+28}" height="{lh+28}" rx="16"
    fill="url(#bez)"/>
  <rect x="{lx-14}" y="{ly-14}" width="{lw+28}" height="{lh+28}" rx="16"
    fill="none" stroke="#00000055" stroke-width="2"/>
  <image x="{lx}" y="{ly}" width="{lw}" height="{lh}"
    xlink:href="data:image/png;base64,{b64}"
    xmlns:xlink="http://www.w3.org/1999/xlink"/>''']
    for b in layout['buttons']:
        if b['shape'] == 'round':
            cx, cy, r = b['cx'], b['cy'], b['r']
            parts.append(f'''
  <circle cx="{cx}" cy="{cy+3}" r="{r}" fill="#00000030"/>
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#btn)"/>
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#7e150f" stroke-width="1.5"/>
  <ellipse cx="{cx}" cy="{cy-r*0.42}" rx="{r*0.62}" ry="{r*0.3}" fill="#ffffff33"/>''')
            if b['label']:
                parts.append(f'''
  <text x="{cx}" y="{cy+r+20}" text-anchor="middle"
    font-family="Helvetica Neue, Helvetica" font-size="12"
    font-weight="bold" fill="#6b6b70" letter-spacing="1">{b['label']}</text>''')
        else:
            x, y, w, h = b['rect']
            parts.append(f'''
  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h//2}" fill="#c9c6bf"/>
  <rect x="{x+3}" y="{y+2}" width="{w-6}" height="{h-6}" rx="{(h-6)//2}" fill="#f4f2ee"/>
  <text x="{x+w//2}" y="{y+h+18}" text-anchor="middle"
    font-family="Helvetica Neue, Helvetica" font-size="11"
    font-weight="bold" fill="#6b6b70" letter-spacing="1">{b['label']}</text>''')
    parts.append('</svg>')
    r = subprocess.run(['rsvg-convert', '--format', 'png'],
                       input=''.join(parts).encode(), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[:300])
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(r.stdout); tmp = f.name
    w, h, rgba = read_png(tmp)
    os.unlink(tmp)
    return w, h, rgba


# ---------------------------------------------------------------- artwork

def load_artwork(zip_path, max_panel=artwork.MAX_PANEL):
    """A MAME external artwork pack: the scans of the handheld plus a
    default.lay placing the emulated screen among them. artwork.py picks
    the view, composites the unit and reports the screen window; see the
    notes there for how the packs differ."""
    return artwork.render(zip_path, max_panel=max_panel)


# What the artwork community calls each press image. The layouts are not
# always renumbered when a driver's ports are, so gnw_helmet's .lay still
# says the Game A button reports IN.0 when the driver has moved it to
# IN.2; the file name did not drift.
PRESS_NAMES = {
    'left-flat': 'left', 'right-flat': 'right',
    'up-flat': 'up', 'down-flat': 'down',
    'grey-flat-1': 'gamea', 'grey-flat-2': 'gameb', 'grey-flat-3': 'time',
    'grey-flat-1p': 'gamea', 'grey-flat-2p': 'gameb', 'grey-flat-3p': 'time',
}


def artwork_tapzones(zip_path, panel, wiring):
    """Tap rectangles straight from the pack's press images.

    artwork.buttons() reports where each button is and which input it
    reports, as an inputtag and inputmask; the wiring says which action
    that input is. Games whose pack carries no press images fall back to
    the hand-measured table below.
    """
    zones = []
    for b in artwork.buttons(zip_path, panel):
        tag, mask = b['tag'], b['mask']
        act = None
        if tag.startswith('IN.'):
            i = int(tag[3:])
            cols = wiring['columns']
            if i < len(cols):
                act = next((a for a, m in cols[i].items() if m == mask), None)
        elif tag.lower() in ('ba', 'b'):
            pin = wiring.get('pins', {}).get(tag.lower())
            act = pin['action'] if pin else None
        if act is None:
            # stale port numbering in the layout: fall back to the name,
            # but only to an action this game actually has
            named = PRESS_NAMES.get((b.get('ref') or '').lower())
            known = {a for c in wiring['columns'] for a in c}
            known |= {p['action'] for p in wiring.get('pins', {}).values()}
            if named in known:
                act = named
        if act:
            zones.append(tuple(b['rect']) + (act,))
    return zones


# Hand-measured fallback, in the .lay's own view coordinates (x, y, w, h,
# action) so they hold at any panel resolution. Only needed for packs
# that ship no press images.
ARTWORK_BUTTONS = {
    'gnw_ball': [
        ( 484.9, 669.0,  87.1, 68.6, 'left'),
        (1349.1, 666.3,  92.3, 71.2, 'right'),
        ( 945.5, 803.5,  73.9, 52.8, 'gamea'),
        (1069.5, 803.5,  73.9, 52.8, 'gameb'),
        (1190.8, 803.5,  73.9, 52.8, 'time'),
    ],
}

# Which retropad buttons stand in for each action. A unit's own buttons
# are the tap zones; this is the fallback for playing on a pad, and only
# the actions a game actually has get wired.
RETROPAD = {
    'start': ['start'], 'pause': ['select'], 'sound': ['l3'],
    'select': ['r2'],
    'gamea': ['start', 'l1'], 'gameb': ['r1'], 'time': ['l2'],
    'left': ['left'], 'right': ['right'], 'up': ['up'], 'down': ['down'],
    'lup': ['up'], 'ldown': ['down'], 'rup': ['x'], 'rdown': ['b'],
    'b1': ['a'], 'b2': ['y'], 'hit': ['y', 'x'], 'jump': ['a'],
    'punch': ['a'], 'fire': ['a'], 'shoot': ['a'],
}


def load_inputs():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'inputs.json')
    return json.load(open(path)) if os.path.exists(path) else {}


def render_game(tmpl, title, wiring, layout, segdefs, tapzones):
    """Fill a game template from the wiring extracted by
    extract_inputs.py. Column indices go 1-based for Lua. Both chips
    read K the same way; only what does the selecting differs, and the
    template says which."""
    cols = wiring['columns']
    fixed = wiring['fixed_column']
    btn, actions = [], []
    for i, col in enumerate(cols):
        for act in sorted(col):
            btn.append("  [ '%s' ] = { %d, %d }," % (act, i + 1, col[act]))
            actions.append(act)
    pins = wiring.get('pins', {})
    for pin in ('ba', 'b'):
        if pin in pins:
            actions.append(pins[pin]['action'])

    presses = []
    for act in sorted(set(actions)):
        keys = RETROPAD.get(act)
        if not keys:
            continue
        cond = ' or '.join('newstate.%s' % k for k in keys)
        presses.append("  if %s then press( '%s' ) end" % (cond, act))

    pinlines, idle = [], {}
    for pin in ('ba', 'b'):
        d = pins.get(pin)
        if not d:
            idle[pin] = 1          # unwired pins are pulled up
            continue
        on, off = (1, 0) if not d['active_low'] else (0, 1)
        idle[pin] = off
        pinlines.append("  %s = held[ '%s' ] and %d or %d"
                        % (pin, d['action'], on, off))

    return (tmpl.replace('@TITLE@', title)
                .replace('@COLS_INIT@', ', '.join(['0'] * max(1, len(cols))))
                .replace('@MUXCOLS@', str(wiring['mux_columns']))
                .replace('@FIXED@', str(fixed + 1 if fixed is not None else 0))
                .replace('@BA_IDLE@', str(idle['ba']))
                .replace('@B_IDLE@', str(idle['b']))
                .replace('@LCD_X@', str(layout['lcd_x']))
                .replace('@LCD_Y@', str(layout['lcd_y']))
                .replace('@LCDS@', '\n'.join(
                    '  { %d, %d },' % (r[0], r[1]) for r in layout['lcds']))
                .replace('@SEGDEFS@', '\n'.join(segdefs))
                .replace('@BUTTONMAP@', '\n'.join(btn))
                .replace('@TAPZONES@', '\n'.join(tapzones))
                .replace('@PRESSES@', '\n'.join(presses) + '\n')
                .replace('@PINS@', ('\n'.join(pinlines) + '\n') if pinlines else ''))

# ---------------------------------------------------------------- game lua

MAIN_LUA = "return system.loadunit 'game'\n"

# ---------------------------------------------------------------- build

# The stock core saves the pixels under every visible sprite into a fixed
# 384k-pixel buffer with no bounds check (RL_BG_SAVE_SIZE in
# retroluxury). A unit whose segments together exceed it dies with
# SIGBUS the moment enough of them light at once, and Tiger units light
# everything at power-on. Panels are scaled until the worst case fits.
SEG_PIXEL_BUDGET = int(384 * 1024 * 0.92)


def main():
    rom_path, segdir, out_path, title = sys.argv[1:5]
    segdirs = segdir.split(',')      # one per screen, top first
    artwork_zip = sys.argv[5] if len(sys.argv) > 5 else None
    shortname = sys.argv[6] if len(sys.argv) > 6 else None
    chip = sys.argv[7] if len(sys.argv) > 7 else 'sm5a'
    max_w = int(sys.argv[8]) if len(sys.argv) > 8 else None
    manifests = []
    for sd in segdirs:
        m = json.load(open(os.path.join(sd, 'segments.json')))
        manifests.append((sd, m, m.pop('_canvas')))
    segdir, manifest, (lw, lh) = manifests[0][0], manifests[0][1], manifests[0][2]

    files = {}

    if artwork_zip:
        panel = load_artwork(artwork_zip,
                             max_panel=(max_w, max_w) if max_w else artwork.MAX_PANEL)
        pw, ph, rgba, lcd = panel.w, panel.h, panel.rgba, panel.lcd
        if len(segdirs) > 1 and len(panel.lcds) < len(segdirs):
            raise SystemExit('%d segment dirs but the artwork has %d screens'
                             % (len(segdirs), len(panel.lcds)))
        layout = {'panel_w': pw, 'panel_h': ph,
                  'lcd_x': lcd[0], 'lcd_y': lcd[1], 'lcd_w': lcd[2], 'lcd_h': lcd[3],
                  'artwork': True,
                  'lcds': [panel.lcds[i] for i in sorted(panel.lcds)],
                  'tapzones_px': []}
        wiring = load_inputs().get(shortname)
        zones = artwork_tapzones(artwork_zip, panel, wiring) if wiring else []
        if zones:
            print('%d tap zones from the pack\'s press images' % len(zones))
        else:
            zones = [panel.to_panel_rect(b[:4]) + (b[4],)
                     for b in ARTWORK_BUTTONS.get(shortname, [])]
            if zones:
                print('%d tap zones from the hand-measured table' % len(zones))
            else:
                print('warning: no tap zones; %s is joypad-only' % shortname)
        layout['tapzones_px'] = zones
        if len(panel.lcds) > len(segdirs):
            print('warning: %s has %d screens but %d segment dirs'
                  % (os.path.basename(artwork_zip), len(panel.lcds), len(segdirs)))
    else:
        wiring0 = load_inputs().get(shortname) or {'columns': [], 'pins': {}}
        layout = panel_layout(wiring0, lw, lh)

    # Buzzer tones. The SM5A drives its piezo by toggling R in software;
    # Ball's bursts measure as half-periods of 4 and 3 machine cycles,
    # i.e. 2048Hz and ~2731Hz squares. Two looped samples, gated by the
    # game when a burst is running. Raw big-endian s16 mono at 44100.
    def square_pcm(freq, amp=7000):
        period = max(2, round(44100 / freq))
        half = period // 2
        cycle = [amp] * half + [-amp] * (period - half)
        loop = (cycle * 8)   # a few periods so the loop point is rare
        return b''.join(struct.pack('>h', v) for v in loop)
    files['sq2048.pcm'] = square_pcm(2048)
    files['sq2731.pcm'] = square_pcm(2731)

    if not layout['artwork']:
        pw, ph, rgba = build_panel(segdir, layout, title)
    # An artwork panel arrives ready: artwork.py keeps a printed LCD
    # background where the pack has one and supplies LCD grey-green
    # where it does not.
    files['background.rle'] = encode_rle(layout['panel_w'], layout['panel_h'], rgba)
    print('panel %dx%d -> %d bytes rle' % (pw, ph, len(files['background.rle'])))

    for i, (sd, m, (cw, ch)) in enumerate(manifests):
        win = panel.lcds[i] if artwork_zip and i in getattr(panel, 'lcds', {}) \
              else (0, 0, layout['lcd_w'], layout['lcd_h'])
        if abs(cw - win[2]) > 2:
            print('warning: screen %d segments rendered %dx%d but its window '
                  'is %dx%d; re-run svg2segs.py at width %d'
                  % (i, cw, ch, win[2], win[3], win[2]))

    segdefs, total, used_px, allsegs = [], 0, 0, []
    for scr, (sd, mani, _c) in enumerate(manifests):
        for name in sorted(mani):
            m = mani[name]
            w, h, px = read_png(os.path.join(sd, m['file']))
            fn = 's%d_%s' % (scr, m['file'].replace('.png', '.rle'))
            files[fn] = encode_rle(w, h, px)
            total += len(files[fn])
            used_px += struct.unpack('>I', files[fn][4:8])[0]
            allsegs.append((scr, name, m, fn))
            o, yy, hh = (int(t) for t in name.split('.'))
            segdefs.append("  { '%s', %d, %d, '%s', %d, %d, %d, %d }," %
                           (name, m['x'], m['y'], fn, o, yy, hh, scr + 1))
    print('%d segments -> %d bytes rle, %d px worst-case' %
          (len(segdefs), total, used_px))
    if used_px > SEG_PIXEL_BUDGET:
        # pixels scale with width squared
        scale = (SEG_PIXEL_BUDGET / used_px) ** 0.5
        print('SEGPX_OVER used=%d budget=%d rescale=%.4f' %
              (used_px, SEG_PIXEL_BUDGET, scale))
        raise SystemExit(3)

    tapzones = []
    if layout['artwork']:
        for (x, y, w, h, act) in layout['tapzones_px']:
            tapzones.append("  { %d, %d, %d, %d, '%s' }," % (x, y, w, h, act))
    else:
        for b in layout['buttons']:
            r = b['rect']
            pad = 14 if b['shape'] == 'round' else 8
            tapzones.append("  { %d, %d, %d, %d, '%s' }," %
                            (r[0]-pad, r[1]-pad, r[2]+2*pad, r[3]+2*pad, b['act']))

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))

    if chip in ('sm511', 'sm512', 'sm530'):
        # melody chips: same shell as the SM510 with the melody ROM and a
        # note-sample bank. The chip can produce 12 notes in 2 octaves;
        # frequencies follow the datasheet tone-cycle table exactly as
        # sm511.lua computes them.
        TONE = [0,0,7,8,8,9,9,10,11,11,12,13,14,14,0,0,
                0,0,8,8,9,9,10,11,11,12,13,13,14,15,0,0,
                0,0,8,8,9,9,10,10,11,12,12,13,14,15,0,0,
                0,0,8,9,9,10,10,11,11,12,13,14,14,15,0,0]
        notes = []
        for note in range(2, 14):
            for shift in (0, 1):
                total = sum(TONE[(d << 4) | note] << shift for d in range(4))
                hz = 32768.0 / total
                if hz not in [n[0] for n in notes]:
                    notes.append((hz, 'mel_%d.pcm' % round(hz)))
        for hz, fn in notes:
            files[fn] = square_pcm(hz)
        notelua = '\n'.join('  { %.2f, \'%s\' },' % (hz, fn) for hz, fn in notes)

        sdefs = ["  { '%s', %d, %d, '%s', %d }," % (name, m['x'], m['y'], fn, scr + 1)
                 for scr, name, m, fn in allsegs]
        wiring = load_inputs().get(shortname)
        if wiring is None:
            raise SystemExit('no input wiring for %r: run extract_inputs.py' % shortname)
        tmpl = open(os.path.join(here, 'game_sm511.lua.tmpl')).read()
        corefile = 'sm510/sm530.lua' if chip == 'sm530' else 'sm510/sm511.lua'
        unitname = 'sm530' if chip == 'sm530' else 'sm511'
        tmpl = tmpl.replace('@CHIP@', chip).replace('@NOTES@', notelua).replace("loadunit 'sm511'", "loadunit '%s'" % unitname).replace('sm511.new', unitname + '.new')
        files['game.lua'] = render_game(tmpl, title, wiring, layout,
                                        sdefs, tapzones).encode()
        files[unitname + '.lua'] = open(os.path.join(root, corefile), 'rb').read()
        files['melody.bin'] = open(os.path.join(os.path.dirname(rom_path),
                                                'melody.bin'), 'rb').read()
    elif chip == 'sm510':
        # SM510: segdefs need only (tag, x, y, rle); segments are looked up
        # by their x.y.z tag in cpu:segments(). Rebuild from the manifest.
        sdefs = ["  { '%s', %d, %d, '%s', %d }," % (name, m['x'], m['y'], fn, scr + 1)
                 for scr, name, m, fn in allsegs]
        wiring = load_inputs().get(shortname)
        if wiring is None:
            raise SystemExit('no input wiring for %r: run extract_inputs.py' % shortname)
        tmpl = open(os.path.join(here, 'game_sm510.lua.tmpl')).read()
        files['game.lua'] = render_game(tmpl, title, wiring, layout,
                                        sdefs, tapzones).encode()
        files['sm510.lua'] = open(os.path.join(root, 'sm510/sm510.lua'), 'rb').read()
    else:
        wiring = load_inputs().get(shortname)
        if wiring is None:
            raise SystemExit('no input wiring for %r: run extract_inputs.py' % shortname)
        tmpl = open(os.path.join(here, 'game_sm5a.lua.tmpl')).read()
        files['game.lua'] = render_game(tmpl, title, wiring, layout,
                                        segdefs, tapzones).encode()
        files['sm5a.lua'] = open(os.path.join(root, 'sm510/sm5a.lua'), 'rb').read()

    files['rom.bin'] = open(rom_path, 'rb').read()

    # main.bs via the repo's own encoder
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, 'main.lua'); dst = os.path.join(td, 'main.bs')
        open(src, 'w').write(MAIN_LUA)
        subprocess.run([os.path.join(root, 'sm510/lua'),
                        os.path.join(root, 'gw-libretro/etc/bsenc.lua'), src, dst],
                       check=True)
        files['main.bs'] = open(dst, 'rb').read()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(files):
            ti = tarfile.TarInfo(name)
            ti.size = len(files[name])
            ti.mtime = 0
            tar.addfile(ti, io.BytesIO(files[name]))
    open(out_path, 'wb').write(bz2.compress(buf.getvalue(), 9))
    print('wrote %s (%d bytes)' % (out_path, os.path.getsize(out_path)))

if __name__ == '__main__': main()
