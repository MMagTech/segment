# segment

Tools for bringing LCD handheld games to the gw-libretro ("Handheld
Electronic Game") core. Two independent tracks:

## 1. A pointer fix for the 59 existing games

gw-libretro's 1.6.0 touch support is unreachable for every shipped game:
they all take the `compatinit` init path, which never reads the pointer,
so taps on the drawn buttons are discarded. The fix delivers clicks to
the games' own mouse handlers.

- Diagnosis, measurements, and desktop reproduction: `FINDINGS.md`
- Upstream report and fix: libretro/gw-libretro issue #84, PR #85
- The patched core is a clone under `gw-libretro/` (branch
  `compatinit-pointer`), not tracked here — see the PR.

## 2. MAME handhelds as self-contained .mgw

MAME emulates ~175 handhelds (every Nintendo Game & Watch, the whole
Tiger catalogue) at chip level, but locked in MAME's ROM+artwork+layout
form. This packages them the way MADrigal's games are packaged: one
self-contained `.mgw`, running on the existing gw core, playable by tap.

- `sm510/sm5a.lua` — a Sharp SM5A CPU core in Lua, ported from MAME's
  BSD-3-Clause implementation, verified instruction-exact against MAME.
- `tools/gw/svg2segs.py` — extracts LCD segments from a romset's SVG.
- `tools/gw/artwork.py`: renders a MAME external artwork pack down to
  one handheld panel and the screen rectangle inside it. Run it on a
  pack to inspect or preview what it finds.
- `tools/gw/build_mgw.py` — packages ROM + segments (+ optional MAME
  external artwork) into a playable `.mgw`.
- `tools/bench/` — a headless libretro harness for verification.

Requirements: `rsvg-convert` (`brew install librsvg`) for the segment
and drawn-panel rendering, and Pillow + numpy for the artwork
compositing.

Games built this way run on the **stock** released core; they carry
their own chip emulator and do not need the fix from track 1.

## Copyright

A tool you run on your own files. ROMs are not distributable (dump your
own units); MAME artwork packs are their artists' work and require
credit. Nothing under `roms/`, `games/`, or `games-out/` is committed.
The MADrigal simulators' obfuscation was his stated wish; no decoder is
shipped.

## License

BSD-3-Clause (see `LICENSE`). The project's own code is free to use,
modify, and redistribute, including commercially, with the notice kept.
The SM5A core (`sm510/sm5a.lua`) is derived from MAME's BSD-3-Clause
CPU code and retains that attribution to "hap"/MAME.
