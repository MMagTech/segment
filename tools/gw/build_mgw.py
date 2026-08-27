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

def build_panel(segdir, layout, title='LCD'):
    """Render the panel with rsvg: silver body, the LCD backdrop inset,
    round action buttons, service pills. Returns (w, h, rgba)."""
    import base64
    bd = open(os.path.join(segdir, 'backdrop.png'), 'rb').read()
    b64 = base64.b64encode(bd).decode()
    W, H = layout['panel_w'], layout['panel_h']
    lx, ly = layout['lcd_x'], layout['lcd_y']
    lw, lh = layout['lcd_w'], layout['lcd_h']
    title_upper = title.upper()[:18]
    parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
  viewBox="0 0 {W} {H}">
  <defs>
    <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#d9d9d3"/>
      <stop offset="1" stop-color="#b9b9b2"/>
    </linearGradient>
  </defs>
  <rect width="{W}" height="{H}" rx="18" fill="url(#body)"/>
  <rect x="{lx-8}" y="{ly-8}" width="{lw+16}" height="{lh+16}" rx="8" fill="#4a4640"/>
  <image x="{lx}" y="{ly}" width="{lw}" height="{lh}"
    xlink:href="data:image/png;base64,{b64}"
    xmlns:xlink="http://www.w3.org/1999/xlink"/>
  <text x="{lx+10}" y="30" font-family="Helvetica, Arial" font-size="22"
    font-weight="bold" fill="#5c5850" letter-spacing="4">{title_upper}</text>''']
    for b in layout['buttons']:
        x, y, w, h = b['rect']
        cx, cy = x + w // 2, y + h // 2
        if b['shape'] == 'round':
            parts.append(f'''
  <circle cx="{cx}" cy="{cy}" r="{w//2}" fill="#6e2a24"/>
  <circle cx="{cx}" cy="{cy-2}" r="{w//2-4}" fill="#a03830"/>
  <text x="{cx}" y="{y+h+18}" text-anchor="middle" font-family="Helvetica"
    font-size="13" font-weight="bold" fill="#5c5850">{b['label']}</text>''')
        else:
            parts.append(f'''
  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h//2}" fill="#7d7a72"/>
  <rect x="{x+2}" y="{y+2}" width="{w-4}" height="{h-6}" rx="{(h-6)//2}" fill="#efeeea"/>
  <text x="{cx}" y="{y+h+14}" text-anchor="middle" font-family="Helvetica"
    font-size="10" fill="#5c5850">{b['label']}</text>''')
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

def load_artwork(zip_path):
    """A MAME external artwork pack: the full unit PNG plus a default.lay
    that positions the emulated screen inside it. Returns the panel rgba
    and the LCD window rectangle in panel-pixel space."""
    import zipfile, re as _re
    z = zipfile.ZipFile(zip_path)
    names = z.namelist()
    lay = z.read([n for n in names if n.lower().endswith('.lay')][0]).decode()

    # largest PNG is the full-unit scan
    pngs = [n for n in names if n.lower().endswith('.png') and '/' not in n]
    art_name = max(pngs, key=lambda n: z.getinfo(n).file_size)
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(z.read(art_name)); tmp = f.name
    aw, ah, argba = read_png(tmp)
    os.unlink(tmp)

    def bounds(tag):
        m = _re.search(r'<%s[^>]*>\s*<bounds ([^/]+)/>' % tag, lay) or \
            _re.search(r'<%s\b[^/]*\bx=' % tag, lay)
        return None
    # screen window: the <screen ... blend="multiply"> bounds
    ms = _re.search(r'<screen index="0" blend="multiply"\s*>\s*<bounds x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"', lay)
    sx, sy, sw, sh = (float(v) for v in ms.groups())
    # art element bounds: the element ref whose PNG is the full unit,
    # identified by matching the element name to the art file stem
    stem = os.path.splitext(art_name)[0]
    me = _re.search(r'<element name="%s"' % _re.escape(stem), lay)
    # its draw bounds in the main view: find first <element ref="stem"> ... <bounds>
    mr = _re.search(r'<element ref="%s"\s*>\s*<bounds x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"' % _re.escape(stem), lay)
    ax, ay, awu, ahu = (float(v) for v in mr.groups())

    scale_x = aw / awu
    scale_y = ah / ahu
    lcd = [round((sx - ax) * scale_x), round((sy - ay) * scale_y),
           round(sw * scale_x), round(sh * scale_y)]
    return aw, ah, argba, lcd

# Button tap zones per game, in full-unit pixel space (x, y, w, h, action).
# The .lay's control group carries only full-image press overlays, so the
# real button rectangles are located directly in the art. action is a K
# bitmask (int) or 'ba'/'b' for the two joystick pins.
ARTWORK_BUTTONS = {
    'gnw_ball': [
        (212,  995, 165, 130, 'b'),    # LEFT red button
        (1850, 990, 175, 135, 'ba'),   # RIGHT red button
        (1085, 1250, 140, 100, 4),     # GAME A
        (1320, 1250, 140, 100, 2),     # GAME B
        (1550, 1250, 140, 100, 1),     # TIME
    ],
}

# ---------------------------------------------------------------- game lua

GAME_LUA = '''-- {title}: generated from the MAME romset by build_mgw.py.
-- The chip is a Lua port of MAME's SM5A core; the segments come from
-- the romset's own SVG. This unit drives the gw runtime directly.

local sm5a = system.loadunit 'sm5a'

-- buzzer: R toggles at tone frequency; count edges per frame and gate
-- a looped square of the measured pitch
local redges, rint, rlastdiv = 0, 0, -1

local cpu = sm5a.new{{
  rom = system.loadbin 'rom.bin',
  read_k  = function() return _K end,
  read_ba = function() return _BA end,
  read_b  = function() return _B end,
  write_r = function( out )
    redges = redges + 1
  end,
}}
cpu:reset()

local snd2048 = system.newsound()
snd2048.data = system.loadbin 'sq2048.pcm'
snd2048.loop = true
local snd2731 = system.newsound()
snd2731.data = system.loadbin 'sq2731.pcm'
snd2731.loop = true
local buzzing = nil

_K, _BA, _B = 0, 1, 1

-- panel
local bgimg = system.newimage()
bgimg.picture.data = system.loadbin 'background.rle'
system.setbackground( bgimg.picture )
-- detach: the panel lives in the background framebuffer now, and a
-- 400k-pixel sprite would overflow the sprite save buffer (compatinit
-- does exactly this after its own setbackground)
bgimg.picture = nil
bgimg.visible = false

-- segments
local LCD_X, LCD_Y = {lcd_x}, {lcd_y}
local segdefs = {{
{segdefs}
}}

local segimgs = {{}}
for i = 1, #segdefs do
  local d = segdefs[ i ]
  local img = system.newimage()
  img.picture.data = system.loadbin( d[ 4 ] )
  img.left = LCD_X + d[ 2 ]
  img.top  = LCD_Y + d[ 3 ]
  img.visible = false
  segimgs[ d[ 1 ] ] = img
end

-- buttons: retropad map and tap rectangles
local tapzones = {{
{tapzones}
}}

local CYCLES_PER_FRAME = 16384 / 60
local acc = 0
local newstate = {{}}
local pointer_was = false

return function()
  -- the core never calls rl_sound_init, so the mixer boots muted;
  -- compatinit unmutes it exactly this way every tick
  if not system.issoundactive() then
    system.resumesounds()
  end

  system.inputstate( newstate )

  local k, ba, b = 0, 1, 1
  if newstate.l1 or newstate.start then k = k | 4 end      -- Game A
  if newstate.r1 then k = k | 2 end                        -- Game B
  if newstate.l2 or newstate.select then k = k | 1 end     -- Time
  if newstate.right or newstate.a then ba = 0 end
  if newstate.left or newstate.b then b = 0 end

  -- taps on the drawn buttons
  if newstate.pointer_pressed then
    local x, y = newstate.pointer_x, newstate.pointer_y
    for i = 1, #tapzones do
      local z = tapzones[ i ]
      if x >= z[ 1 ] and x < z[ 1 ] + z[ 3 ] and
         y >= z[ 2 ] and y < z[ 2 ] + z[ 4 ] then
        local what = z[ 5 ]
        if what == 'ba' then ba = 0
        elseif what == 'b' then b = 0
        else k = k | what end
        break
      end
    end
  end

  _K, _BA, _B = k, ba, b

  redges = 0
  acc = acc + CYCLES_PER_FRAME
  local n = acc // 1
  acc = acc - n
  local div0 = cpu.div
  cpu:run( n )

  -- gate the buzzer: a burst is running when R toggled this frame.
  -- Pitch from the toggle rate: edges per frame over the frame's
  -- cycles gives the frequency directly.
  if redges >= 4 then
    local hz = redges * 16384 / (2 * n)
    local want = (hz > 2350) and snd2731 or snd2048
    if buzzing ~= want then
      if buzzing then system.stopsounds( -1 ) end
      system.playsound( want, -1 )   -- -1: pick a free channel
      buzzing = want
    end
  elseif buzzing then
    system.stopsounds( -1 )
    buzzing = nil
  end

  -- mirror chip segment state into sprite visibility
  local on = (cpu.bp & 1) == 1
  for i = 1, #segdefs do
    local d = segdefs[ i ]
    local o = d[ 5 ]; local yy = d[ 6 ]; local hh = d[ 7 ]
    local nib = (hh == 1) and cpu.ox[ o ] or cpu.o[ o ]
    segimgs[ d[ 1 ] ].visible = on and ((nib >> yy) & 1) == 1
  end

  return true
end
'''

MAIN_LUA = "return system.loadunit 'game'\n"

# ---------------------------------------------------------------- build

# SM510 games mux K inputs through the S strobe. Per game, map a logical
# action to (column_index, k_bit); read_k ORs the pressed bits of columns
# S has selected. From each game's INPUT_PORTS in the MAME driver.
INPUT_MAPS_SM510 = {
    'gnw_stennis': {
        'down': (0, 0x1), 'up': (0, 0x2), 'hit': (0, 0x8),
        'time': (1, 0x1), 'gameb': (1, 0x2), 'gamea': (1, 0x4),
        'alarm': (1, 0x8),
    },
}


def main():
    rom_path, segdir, out_path, title = sys.argv[1:5]
    artwork_zip = sys.argv[5] if len(sys.argv) > 5 else None
    shortname = sys.argv[6] if len(sys.argv) > 6 else None
    chip = sys.argv[7] if len(sys.argv) > 7 else 'sm5a'
    manifest = json.load(open(os.path.join(segdir, 'segments.json')))
    lw, lh = manifest.pop('_canvas')

    files = {}

    if artwork_zip:
        pw, ph, rgba, lcd = load_artwork(artwork_zip)
        layout = {'panel_w': pw, 'panel_h': ph,
                  'lcd_x': lcd[0], 'lcd_y': lcd[1], 'lcd_w': lcd[2], 'lcd_h': lcd[3],
                  'artwork': True,
                  'tapzones_px': ARTWORK_BUTTONS.get(shortname, [])}
    else:
        layout = {
            'panel_w': max(480, lw), 'panel_h': lh + 190,
            'lcd_x': 0, 'lcd_y': 50, 'lcd_w': lw, 'lcd_h': lh, 'artwork': False,
            'buttons': [
                {'label': 'LEFT',   'shape': 'round', 'rect': [36,  lh+80, 64, 64], 'act': 'b'},
                {'label': 'RIGHT',  'shape': 'round', 'rect': [560, lh+80, 64, 64], 'act': 'ba'},
                {'label': 'GAME A', 'shape': 'pill',  'rect': [566, 10, 64, 22], 'act': 4},
                {'label': 'GAME B', 'shape': 'pill',  'rect': [478, 10, 64, 22], 'act': 2},
                {'label': 'TIME',   'shape': 'pill',  'rect': [390, 10, 64, 22], 'act': 1},
            ],
        }

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
    else:
        # The unit's screen window is dark in the scan; paint it with the
        # classic LCD grey-green so the dark segments read against it, the
        # way an idle Game & Watch panel looks.
        lx, ly, lwd, lhd = layout['lcd_x'], layout['lcd_y'], layout['lcd_w'], layout['lcd_h']
        LCD = (150, 161, 143)
        PW = layout['panel_w']
        for yy in range(ly, ly + lhd):
            base = (yy * PW + lx) * 4
            for xx in range(lwd):
                o = base + xx * 4
                rgba[o] = LCD[0]; rgba[o+1] = LCD[1]; rgba[o+2] = LCD[2]; rgba[o+3] = 255
    files['background.rle'] = encode_rle(layout['panel_w'], layout['panel_h'], rgba)
    print('panel %dx%d -> %d bytes rle' % (pw, ph, len(files['background.rle'])))

    segdefs, total = [], 0
    for name in sorted(manifest):
        m = manifest[name]
        w, h, px = read_png(os.path.join(segdir, m['file']))
        fn = m['file'].replace('.png', '.rle')
        files[fn] = encode_rle(w, h, px)
        total += len(files[fn])
        o, yy, hh = (int(t) for t in name.split('.'))
        segdefs.append("  { '%s', %d, %d, '%s', %d, %d, %d }," %
                       (name, m['x'], m['y'], fn, o, yy, hh))
    print('%d segments -> %d bytes rle' % (len(segdefs), total))

    tapzones = []
    if layout['artwork']:
        for (x, y, w, h, act) in layout['tapzones_px']:
            actlua = "'%s'" % act if isinstance(act, str) else str(act)
            tapzones.append('  { %d, %d, %d, %d, %s },' % (x, y, w, h, actlua))
    else:
        for b in layout['buttons']:
            act = b['act']
            actlua = "'%s'" % act if isinstance(act, str) else str(act)
            r = b['rect']
            pad = 14 if b['shape'] == 'round' else 8
            tapzones.append('  { %d, %d, %d, %d, %s },' %
                            (r[0]-pad, r[1]-pad, r[2]+2*pad, r[3]+2*pad, actlua))

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))

    if chip == 'sm510':
        # SM510: segdefs need only (tag, x, y, rle); segments are looked up
        # by their x.y.z tag in cpu:segments(). Rebuild from the manifest.
        sdefs = []
        for name in sorted(manifest):
            m = manifest[name]
            fn = m['file'].replace('.png', '.rle')
            sdefs.append("  { '%s', %d, %d, '%s' }," % (name, m['x'], m['y'], fn))
        imap = INPUT_MAPS_SM510.get(shortname, {})
        ncols = max((c for c, _ in imap.values()), default=0) + 1
        buttonmap = '\n'.join("  ['%s'] = { %d, %d }," % (a, c, b)
                              for a, (c, b) in imap.items())
        tmpl = open(os.path.join(here, 'game_sm510.lua.tmpl')).read()
        game = (tmpl.replace('@TITLE@', title)
                    .replace('@COLS_INIT@', ', '.join(['0'] * ncols))
                    .replace('@LCD_X@', str(layout['lcd_x']))
                    .replace('@LCD_Y@', str(layout['lcd_y']))
                    .replace('@SEGDEFS@', '\n'.join(sdefs))
                    .replace('@BUTTONMAP@', buttonmap)
                    .replace('@TAPZONES@', '\n'.join(tapzones)))
        files['game.lua'] = game.encode()
        files['sm510.lua'] = open(os.path.join(root, 'sm510/sm510.lua'), 'rb').read()
    else:
        files['game.lua'] = GAME_LUA.format(
            title=title, lcd_x=layout['lcd_x'], lcd_y=layout['lcd_y'],
            segdefs='\n'.join(segdefs), tapzones='\n'.join(tapzones)).encode()
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
