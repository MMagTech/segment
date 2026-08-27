# segment

Tools that turn MAME's chip-level LCD handheld emulation (every Nintendo
Game & Watch, the Tiger Electronics catalogue, Konami, Elektronika) into
self-contained `.mgw` games for the **stock, unmodified** gw-libretro
("Handheld Electronic Game") core in RetroArch.

Each built game packs the real ROM, a Sharp CPU core written in Lua and
verified instruction-exact against MAME, the community's scanned unit
artwork, working touch buttons, and sound into one file. Nothing here
patches the core: the chip emulator travels inside the game, so the
result plays anywhere the existing 59 .mgw games play. You bring your
own ROMs and artwork packs; this repository is only the machinery.

## Building a game (no expertise required)

You need: python3 with Pillow and numpy, and `rsvg-convert`
(`brew install librsvg` on a Mac, `apt install librsvg2-bin` on Linux).
You supply: a MAME romset zip for the game, named as MAME names it
(e.g. `gnw_ball.zip`), and optionally the matching MAME artwork pack.

One command:

    python3 tools/gw/convert.py gnw_ball.zip gnw_ball_artwork.zip

That prints what it finds at each step and writes
`Ball (Nintendo).mgw`, which you drop into RetroArch like any other
game for the "Handheld Electronic Game" core. Leave the artwork zip off
and the game gets a clean generated shell instead of the photographed
unit; run the command again with the pack later and the same game
upgrades in seconds. If a gw-libretro core binary is on your machine
the tool finishes by booting the game headless and pressing Start, and
refuses to hand you a file that does not respond.

`COVERAGE.md` tracks every game the MAME driver knows: which are
built, which wait on an artwork scan, which wait on a ROM dump, and
which wait on a chip port.

## How it works (the semi-technical version)

The gw-libretro core is a small Lua runtime. The 59 games it shipped
with are hand-written simulations; this project instead ports the
actual Sharp chips to Lua and packs the emulator inside each game file:

- `sm510/sm5a.lua`, `sm510/sm510.lua`, `sm510/sm511.lua` — the Sharp
  SM5A, SM510 and SM511/SM512 (melody) cores, each verified by lockstep
  instruction trace against MAME and by rendering the lit-segment set
  through the romset's own SVG against MAME's snapshot. `sm530.lua` is
  written to the same recipe, awaiting assets to verify against.
- `tools/gw/svg2segs.py` — extracts every LCD segment from a romset's
  SVG as a positioned bitmap.
- `tools/gw/artwork.py` — renders a MAME external artwork pack (any of
  the wildly varied layout shapes in circulation) down to one panel
  image plus each screen's window, and locates the physical buttons by
  compositing the pack's own pressed-state overlays.
- `tools/gw/extract_inputs.py` — reads every game's button wiring
  (input matrix columns, strobe, the BA/B pins and their polarity) out
  of MAME's driver source into `inputs.json`, which ships here so users
  never need MAME's source.
- `tools/gw/build_mgw.py` — packages ROM + segments + panel + wiring
  into the .mgw, generating the per-game Lua shell from templates, and
  sizing everything to fit the stock core's fixed sprite budget.
- `tools/gw/convert.py` — the one-command wrapper around all of the
  above, ending in a headless boot-and-respond self-test.
- `tools/bench/` — the headless libretro harness used for every
  verification claim: it can prove a tap on the drawn button produces
  output byte-identical to the equivalent joypad press.

`FINDINGS.md` is the full technical journal: the chip-core
verifications, the artwork-format survey, the stock core's 384k-pixel
save-under buffer (and the three distinct ways exceeding it presents),
and every other trap so the next person does not pay for it twice.

## Two independent tracks

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
