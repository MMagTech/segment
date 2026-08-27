#!/usr/bin/env python3
"""Builds a synthetic .mgw that overruns gw-libretro's sprite save-under
buffer, for the RL_BG_SAVE_SIZE bug report. Contains NO game ROM and NO
artwork: just a few hundred solid-colour sprites, all made visible, so
the saved-pixel total sails past the 384k-pixel budget with nothing
copyrighted inside. Drop the output into RetroArch's Handheld Electronic
Game core and it corrupts the framebuffer, then SIGBUSes.

  python3 upstream/make_overflow_repro.py rl_bg_save_overflow.mgw
"""
import bz2, io, struct, sys, tarfile

# Many overlapping opaque sprites, the way a Tiger unit lights ~130
# full-screen segment layers. Each visible sprite's covered pixels are
# saved into the shared 384k buffer; the total, not any one sprite, is
# what overruns. 260 sprites of 60k px each = ~15.6M saved px.
NSPRITES = 260
SW = SH = 250


def solid_rle(w, h, rgb565):
    """A fully-opaque rl_image of one colour: every row is a single
    opaque run, so 'used' = w*h and the blitter saves all of it."""
    stream, offsets = io.BytesIO(), []
    for _ in range(h):
        offsets.append(stream.tell())
        stream.write(struct.pack('>HH', 1, 1))          # 1 row-marker, 1 run
        stream.write(struct.pack('>H', 4 << 13 | w))    # type 4 (opaque), w px
        stream.write(struct.pack('>H', rgb565) * w)
    out = io.BytesIO()
    out.write(struct.pack('>HH', w, h))
    out.write(struct.pack('>I', w * h))                 # used pixels
    for o in offsets:
        out.write(struct.pack('>I', o))
    out.write(stream.getvalue())
    return out.getvalue()


GAME_LUA = """-- Synthetic overflow repro. No ROM, no artwork: NSPRITES opaque
-- sprites, all visible, so rl_image_blit's saved-pixel total
-- (RL_BG_SAVE_SIZE, 384k) is overrun by the sum. Slight overrun
-- corrupts the framebuffer; this much overruns hard.
local imgs = {}
for i = 1, NSPRITES do
  local img = system.newimage()
  img.picture.data = system.loadbin 'block.rle'
  img.left = (i * 3) % 200
  img.top  = (i * 5) % 200
  img.visible = true
  imgs[i] = img
end
return function() return true end
"""

MAIN_LUA = "return system.loadunit 'game'\n"


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'rl_bg_save_overflow.mgw'
    files = {
        'canvas.rle': solid_rle(1600, 1000, 0xC618),    # grey background
        'block.rle': solid_rle(SW, SH, 0xF800),         # opaque red
        'game.lua': GAME_LUA.encode(),
        'main.bs': MAIN_LUA.encode(),                    # plain lua is accepted
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(files):
            ti = tarfile.TarInfo(name)
            ti.size = len(files[name]); ti.mtime = 0
            tar.addfile(ti, io.BytesIO(files[name]))
    open(out, 'wb').write(bz2.compress(buf.getvalue(), 9))
    print('wrote %s: %d sprites x %dx%d = %d saved px vs 384k budget'
          % (out, NSPRITES, SW, SH, NSPRITES * SW * SH))


if __name__ == '__main__':
    main()
