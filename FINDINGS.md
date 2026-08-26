# LCD handhelds: research handoff

Everything below was established on 2026-08-25/26 while adding Game &
Watch support to a separate iOS app. It is written down because the
findings cost a night to establish and none of them are documented
anywhere public. This project is about **contributing to the community**,
not about that app.

Read this first; it should save you rediscovering all of it.

---

## The two opportunities

### 1. A real bug in gw-libretro: every game ignores taps

**gw-libretro** (the "Handheld Electronic Game" core) runs 59 hand-written
LCD game simulators by MADrigal. Its changelog says, at version 1.6.0:

> Added support for mouse and touch screens.
> * All buttons can be activated with clicks and taps

That is false for the entire shipped library, and the reason is
structural rather than accidental. Measured 2026-08-26: all 59 games
take the path that has no pointer support.

The core has **two init paths**:

- `gwlua/lua/system.lua` — the modern one. Its returned per-frame closure
  reads `pointer_pressed` / `pointer_x` / `pointer_y`, hit-tests the tap
  against each registered control's rectangle, and dispatches the key
  events. Tapping the drawn GAME A button works.
- `gwlua/lua/compatinit.lua` — the older one, used when a game calls
  `system.init` with a non-table first argument. **It contains zero
  references to `pointer`.** Every tap is discarded.

The dispatch is at `system.lua:494`, `if type( background ) ~= 'table'`.
Passing a config table takes the modern path; passing anything else hands
off to `compatinit`. **Every one of the 59 published games passes an
image**, `unit1.form1.im_background` (or `im_background_open` on the
Multi Screen units), so every one of them lands on the path with no
pointer support. The modern path is real, tested code that nothing in the
shipped library reaches.

So on a phone or any touchscreen, none of these games can be played by
tapping the artwork, and the changelog says otherwise.

**Evidence.** Measured with the headless harness described below, on the
released core, four games including the menu-less *Egg*:

| Game | tap positions swept | changed output | joypad Select |
|---|---|---|---|
| Parachute (Nintendo, Wide Screen) | 35 | **0** | changed |
| Donkey Kong (Coleco) | 15 | **0** | changed |
| Egg (Nintendo, Wide Screen) | 15 | **0** | changed |
| Mickey Mouse (Nintendo, Wide Screen) | 15 | **0** | changed |

A sweep matters because a single missed coordinate is indistinguishable
from an ignored tap. Aiming precisely also fails: *Parachute* draws GAME A
at (554, 43, 40, 29) on a 658x395 panel, and a press held 30 frames on its
centre leaves the frame hash **byte-identical** to a run with no input at
all, while a joypad press changes it immediately.

The decisive number is that the core *is* listening. Over 600 frames it
made **1800 pointer queries**, three per frame, all on port 2, and the
harness answered **90** of them with a press at the button's centre.
Polled, delivered, discarded.

**A warning about proving this.** An earlier attempt reported the same
result from a harness whose pointer branch was unreachable dead code, so
it had answered every one of those 1800 queries with zero. A frame hash
cannot tell "the core ignored the tap" from "nothing sent a tap", and the
two conclusions look identical. The harness now reports `pointer_queries`
and `pointer_answered` for exactly this reason; a run where
`pointer_answered` is 0 has measured nothing. Check it before believing
any negative result here.

Reproduce it on a desktop RetroArch build with any of the 59 and a mouse:
clicking the drawn GAME A does nothing, while Select-then-Start (the hand
menu) works.

**Why this is a good contribution:** it is small, self-contained, in one
Lua file, and it finishes a feature the author already built and clearly
wants. The controls it needs to hit-test are already declared by every
game (again, see below).

### 2. The bigger idea: bring MAME's ~116 other handhelds into this format

MAME emulates these machines properly, at chip level:

| MAME driver | Chip | Games |
|---|---|---|
| `hh_sm510` | Sharp SM510/SM511 | **175** |
| `hh_tms1k` | TI TMS1000 | 127 |
| `hh_hmcs40` | Hitachi HMCS40 | 41 |
| `hh_ucom4` | NEC µCOM4 | 26 |
| plus `hh_pic16`, `hh_pps41`, `hh_rw5000`… | | ~40 more |

`hh_sm510`'s 175 include **every Nintendo Game & Watch** (Ball, Chef,
Octopus, Fire, Judge, the whole line) and **the entire Tiger Electronics
catalogue** (Batman, Robocop, Double Dragon, X-Men, Sonic, TMNT…).

MADrigal's 59, by contrast, are mostly *clones*: 25 VTech, plus Gakken,
Coleco, Mattel, Elektronika, Tomytronic, Bandai, Epoch, and only 16
genuine Nintendo units. **The two libraries barely overlap.** MAME has
the famous ones.

**Why MAME's are awkward to live with:** each game is three separate
things from three sources, a ROM zip (`gnw_ball.zip`), a community-made
external artwork pack, and a layout. Without artwork you get floating
LCD segments on black. Whether the buttons are clickable depends entirely
on whoever drew the artwork; MAME's *built-in* layouts for these games
(`hh_sm510_dualh.lay` etc.) are a bare LCD frame with **no clickable
elements at all** (verified: zero `inputtag`/`inputmask`).

**The idea:** package them the way `.mgw` games are packaged, one
self-contained file per game, artwork baked in, running on the existing
gw core. Then they behave exactly like MADrigal's 59.

**Why it's feasible, and what's missing.** A `.mgw` contains game logic,
LCD segment sprites, sound and button geometry. MAME has the sprites (in
its SVG), the sound and the buttons. The only missing piece is the *logic*,
which in MAME is a ROM plus a chip emulator. So:

> Write **one SM510 emulator in Lua**, once. Then every game becomes
> that emulator + the ROM blob + converted sprites, packaged as a single
> `.mgw`. Nothing hand-written per game.

Performance is not the problem: the SM510 runs at about **32 kHz**, slow
enough that a Lua interpreter keeps up comfortably. It is a simple 4-bit
MCU and MAME's own implementation is open source to work from. The hard
part is *accuracy*, "close enough" shows up as games that subtly
misbehave.

**The artwork problem is already solved by someone else.**
[LCD-Game-Shrinker](https://github.com/bzhxx/LCD-Game-Shrinker) takes
MAME romsets plus artwork and automatically "extracts LCD segments from
Scalable Vector Graphics", downscales, reduces palettes, and emits packed
data files. It targets the modded Game & Watch hardware rather than
`.mgw`, but the SVG-to-sprites pipeline is exactly the piece that looked
laborious, and it exists. Some games there need a per-game Python script
in its `custom/` directory, so expect a tail of manual cases.

---

## The `.mgw` format, reverse-engineered

A `.mgw` file is a **bzip2-compressed V7 tar**. (macOS `tar` may refuse
it; Python's `tarfile` may too. Walking the 512-byte headers by hand is
reliable: name at offset 0, size as octal at offset 124, body at 512.)

Entries:

| Entry | What it is |
|---|---|
| `main.bs` | Entry point: keymap, menu table, timers, sound loading, `system.init` call |
| `unit1.bs` | The game itself: every control's geometry, every handler, all the logic |
| `*.pcm` | Raw audio samples |
| `*.rle` | Images: LCD segments, button pressed-states, the panel background |

`.bs` files are Lua obfuscated with
[bstree](https://github.com/leiradel/bstree). `.rle` images start with
**two big-endian uint16s: width, height**.

The Lua was produced from MADrigal's original Delphi source with
[pas2lua](https://github.com/leiradel/pas2lua). **`etc/bsenc.lua` ships
in the gw-libretro repo**, so authoring new `.bs` files is possible with
public tooling.

### Reading a game's own source

`gwlua/bsreader.c` decodes `.bs`. A ~20-line C program that calls
`bsnew()` then loops on `bsread()` dumps readable Lua to stdout. Compile
it against `gwlua/bsreader.c` with `-Igwlua -Ilua/src`.

**Ethical note:** the core's README says bstree "was also specifically
written to obfuscate the generated Lua source code **as per MADrigal's
request**." Reading it privately to understand the format is one thing;
publishing a decoder works against the original author's explicit wish.
Recommend not shipping one.

### What the source tells you

`main.bs` declares the control mapping in plain terms:

```lua
local keymap = {
  up   = { forms.vk_left, forms.vk_up,    "Northwest" },
  down = { forms.vk_left, forms.vk_down,  "Southwest" },
  x    = { forms.vk_right, forms.vk_up,   "Northeast" },
  b    = { forms.vk_right, forms.vk_down, "Southeast" },
  l1   = { 49,                            "Game A" },
  r1   = { 50,                            "Game B" },
  l2   = { 51,                            "Time" }
}

local menu = {
  { unit1.form1.btn_game_a_down, "Game A", 49 },
  { unit1.form1.btn_game_b_down, "Game B", 50 },
  { unit1.form1.btn_mode_down,   "Time",   51 }
}
```

`unit1.bs` carries every control's exact rectangle, and even its hint
text:

```lua
self.btn_game_a_top.left = 280
self.btn_game_a_top.top = 305
self.btn_game_a_top.width = 38
self.btn_game_a_top.height = 21
self.btn_game_a_top.hint = "click to start game A"
self.btn_game_a_top.onmousedown = ( self.btn_game_a_topmousedown )
```

Note the `onmousedown`: **the games themselves have always had mouse
handlers.** They were desktop applications. It is the core's compat path
that fails to deliver the click.

### The hand menu (how games actually start)

When a game declares a `menu` table, `compatinit` **deletes** the direct
Game A / Game B / Time keys from the keymap. So no single button press
starts those games. Instead the framework runs a hand cursor:

- **Select** summons the hand, and each further Select advances it
- **Start** presses whatever the hand points at
- **Start** alone, with no menu open, shows the game's own help overlay
  drawn on a controller diagram, which is the fastest way to learn any
  game's controls

Menu-less games (an older generation, e.g. Nintendo's *Egg*) keep direct
shoulder keys instead: L1/R1/L2/R2 map straight to Game A / Game B /
Time / ACL.

### Control shapes vary by manufacturer

Most games draw four separate corner buttons. Some draw a **single
joystick**, under at least three different names in the source:
`btn_joystick_top`, `btn_pad_top`, or a cross split into `btn_cross_h`
and `btn_cross_v` whose union is the same shape. Anything walking these
games generically must handle all of them.

### Other practical notes

- **No save states.** `retro_serialize` returns false, `serialize_size`
  is 0. There is a small key/value SRAM for settings and scores.
- **The pointer is read on port 2**, not port 0 (stated in the core's
  own `retro_controller_info`).
- **Geometry changes mid-run.** The core calls `SET_GEOMETRY` per game
  and again for its built-in zoom (L/R). A frontend must handle a
  resolution change after load.
- **Frames are RGB565.**
- **The splash eats input** for several seconds at boot; input registered
  from around frame 240 in most games, later in some.
- **Canvases are small and vary per game** (e.g. 562×374, 653×392,
  671×747), and roughly a third are portrait-shaped.
- **59 of MADrigal's 63 simulators were ported.** The other 4 exist only
  as Windows executables on his site; he released all 63 source codes in
  2019, so completing the set is mechanically possible with `pas2lua`.

---

## Licensing

- **gw-libretro: zlib/libpng.** Extremely permissive: use, modify,
  redistribute, commercial included. Conditions are only: don't claim you
  wrote the original, mark altered versions as altered, keep the notice.
  Nothing blocks building on it.
- **MAME: GPL**, and MAME ROMs are **not distributable**. Any converter
  must be a tool people run on their own files, exactly as
  LCD-Game-Shrinker is.
- **Artwork packs** have their own authors (hydef, DarthMarino are named
  by LCD-Game-Shrinker). Credit and permission matter.
- The MADrigal simulators are his work; the obfuscation request above is
  a stated wish worth respecting.

---

## Tooling worth bringing with you

All of this was written in another (MIT-licensed, same author) project,
so copying it here is free. It is listed in order of how much it matters.

### The headless libretro harness — take this first

`libretro_bench.c`, about 630 lines of C, no dependencies. It dlopens any
libretro core, runs a ROM for N frames with no window and no device, and
reports per-frame `retro_run` wall time, audio frames produced, geometry,
and **a hash of every frame** so two runs can be compared mechanically for
"did the output actually change".

It is the reason the findings above are facts rather than guesses. Its
flags matter here:

- `-f N` frames, `-s DIR` system dir, `-d DIR --dump-at a,b,c` dumps
  frames as PPM
- `-i frame,retropad_id,hold` presses a **joypad** button on a schedule
- `-tap frame,x,y,hold[,port]` presses a **pointer** at a coordinate,
  with a negative hold meaning hover-without-clicking

Those last two are what proved the tap gap. It also reports
`pointer_queries` and `pointer_answered`, which is what makes a negative
result trustworthy rather than merely quiet: the first says the core asked,
the second says the harness answered. Both were added after the original
pointer branch turned out to be unreachable, having sat below a guard that
had already returned for anything that was not a joypad on port 0.

For this project it is also how you would test an SM510 implementation:
run MAME and your version on the same ROM and compare frame hashes, with
no human squinting at screenshots.

One caveat, stated in its own header: it deliberately answers the
environment the way that app's frontend does, including **not** answering
`SET_VARIABLES` / `SET_CORE_OPTIONS`, so cores fall back to compiled-in
defaults. For neutral upstream work you may want to answer those.

### The `.bs` decoder

`bsdump.c`, about 15 lines, links against the core's own
`gwlua/bsreader.c`. Prints a game's readable Lua to stdout. See the
ethical note above about not publishing it.

### The extraction scripts

Three Python tools, roughly 400 lines total, worth reading for technique
rather than copying wholesale (their paths and output format are specific
to that app):

- **`extract_hotspots.py`** — the genuinely novel one. Finds where a
  game's buttons are *drawn* with no source access at all: press each
  input in the harness, diff the frames against an unpressed run, mask
  out the game's own idle animation (clocks and demos) so it cannot
  masquerade as a button, and the pixels that remain are the button.
  Mapped 55 of 59 games unattended. The technique transfers to any core
  that draws its own controls.
- **`extract_menu.py`** — reads `main.bs` and `unit1.bs` for the menu
  table and each control's rectangle. Got all 59.
- **`extract_direct.py`** — the fallback for games that light nothing in
  idle mode: read geometry straight from source, and quarter a single
  drawn joystick into four directions.

Between them, 59 of 59 games were mapped with no button placed by hand.

---

## Suggested order of work

1. ~~Reproduce the tap gap in plain RetroArch on a desktop~~ **Done
   2026-08-26.** RetroArch 1.22.2, Parachute, core commit dddc9d5: a real
   mouse click on the drawn GAME A leaves the frame byte-identical, then
   keyboard Select summons the hand and Start launches the game in the
   same session. Screenshots in `upstream/repro/`; issue draft in
   `upstream/gw-libretro-tap-issue.md`.
2. ~~File it upstream~~ **Done 2026-08-26:**
   https://github.com/libretro/gw-libretro/issues/84
3. ~~Optionally write the fix~~ **Done 2026-08-26**, on branch
   `compatinit-pointer`, submitted upstream as
   https://github.com/libretro/gw-libretro/pull/85. It takes a different shape than first sketched: rather
   than porting the modern path's hit test, it delivers clicks to the
   games' own Delphi mouse handlers, which every control still carries.
   extctrls records created images as a registry; compatinit hit-tests
   topmost-first and calls onmousedown/onmouseup/onclick; image.c learns
   to store onclick instead of dropping it. Verified 59/59 by
   `tools/gw/validate_tap_fix.py`: every game's tap works, and no-tap
   runs stay byte-identical to the unpatched core.

   Traps that cost time, for whoever touches this next: the pointer
   arrives in framebuffer space unzoomed but game space zoomed, and
   setbackground pads narrow games to 480 wide, shifting everything; the
   clamshell ports never run their lid-opening code, so their hotspots
   sit permanently invisible (hence hit-testing pictureless images
   regardless of the visible flag); and the Makefile misses the
   functions.o dependency on the baked-in Lua headers, so touch
   gwlua/functions.c after regenerating any .h or you will validate a
   stale binary.
4. Only then consider the SM510 project. Start with **one game**,
   end to end, `gnw_ball` is the simplest, before any pipeline.

---

## Useful links

- gw-libretro: https://github.com/libretro/gw-libretro
- Prebuilt game files: https://bot.libretro.com/assets/cores/HandheldElectronicGame/
- pas2lua: https://github.com/leiradel/pas2lua
- bstree: https://github.com/leiradel/bstree
- MADrigal's simulators: https://www.madrigaldesign.it/sim/
- LCD-Game-Shrinker: https://github.com/bzhxx/LCD-Game-Shrinker
- MAME `hh_sm510.cpp`: https://github.com/mamedev/mame/blob/master/src/mame/handheld/hh_sm510.cpp
- MAME layout format (clickable elements):
  https://docs.mamedev.org/techspecs/layout_files.html

---

## SM5A in Lua: the spike landed (2026-08-26)

The first slice of the MAME-handhelds idea exists and is verified.
`sm510/sm5a.lua` is a complete Sharp SM5A core in about 400 lines of
plain Lua 5.3, ported from MAME's BSD-3-Clause implementation (hap).
Ball (gnw_ball, the AC-01 ROM) boots and plays in it.

Verification, all against MAME 0.289 on the same ROM:

- **Instruction-exact:** a lockstep trace of PC/ACC/BL/BM/C matches
  MAME for the first 40,000 instructions. The only difference in 3
  emulated seconds is one extra iteration of a divider-wait loop,
  a timer-phase artifact of modeling the divider inline rather than on
  an async timer; the streams realign immediately and completely.
- **Display-exact:** at 5s the lit-segment set, pushed through the
  romset's own SVG, renders the same attract screen as MAME's
  snapshot, dot for dot (`sm510/evidence/`).
- **Playable:** holding K=4 (Game A) for a quarter second starts the
  game; the display switches to the juggler with two balls in flight.

Things learned that the next session needs:

- Ball is **SM5A**, not SM510: the early silver/gold series all are.
  The SM510 proper (Tiger, later Nintendo) is a sibling core to port
  next; MAME's sources put the differences in sm510core/sm510op vs
  sm500/sm5a, all small.
- The romset zip carries **both** halves: the ROM and the segment SVG,
  whose element titles are exactly MAME's `o.y.h` output tags. No
  external artwork needed for the LCD itself; external art is only the
  cabinet around it.
- MAME as oracle: `-debug -debugscript` with
  `trace f,maincpu,noloop,{tracelog "%04X A=%X ...",pc,acc,...}` gives
  a per-instruction state log headlessly. Two traps: a bare `a` in a
  tracelog expression is hex ten, not ACC, and produced a trace where
  every accumulator read as A; and an empty options slot in the trace
  command silently truncates the log.
- The debugger eats the first instruction (the trace starts one in),
  so drop line one of a local trace before diffing.
- MAME snapshots at N seconds via `-video none -sound none -str N
  -snapshot_directory D` run headless at full speed.

Still open, in order: sound (the R output is modeled but unheard),
long-run and interactive trace verification, the SM510 sibling core,
and the packaging half: SVG segments to .rle sprites, a generated
main.bs/unit1.bs shell, and the buttons, which the gw core can now
deliver taps to. None of it is blocked on anything external.

## The first MAME-derived .mgw exists (2026-08-26, same day)

`games-out/Ball (Nintendo, Silver).mgw`, 25KB, built by
`tools/gw/svg2segs.py` + `tools/gw/build_mgw.py` from the romset alone.
It boots, shows its splash, and plays Game A on the released gw core,
verified headless: a joypad Game A press and a tap on the drawn GAME A
pill produce byte-identical output hashes, because the game reads the
pointer itself and needs neither compatinit nor the hand menu.

How it is put together: main.bs is a one-line bs-encoded stub (the core
demands main.bs, but units it loads may be plain .lua, so everything
readable stays readable); game.lua builds the panel, creates one image
per segment, maps retropad and tap zones to the chip's K/BA/B pins, and
ticks the CPU 16384/60 cycles per frame; sm5a.lua is the verified core,
unmodified; rom.bin is the chip ROM; the .rle images are encoded by the
build script (format learned from rl_image.c: big-endian u16 w,h, u32
used, u32 row offsets, rows of [1][nruns][type<<13|count] with five run
types, transparent/25/50/75/opaque).

Traps for the next machine:

- Inkscape SVGs fight display overrides twice: a style attribute beats
  a display attribute, and within a style the LAST declaration wins, so
  hiding a layer means editing display:inline to display:none, not
  prepending.
- After system.setbackground, set the image's picture to nil, exactly
  as compatinit does: a full-panel sprite overflows the sprite
  save-under buffer and dies in rl_image_blit.
- The gw core swallows tick errors silently (l_pcall to a log nobody
  sees); a game that loads but shows a frozen splash is usually a Lua
  error on the first tick. Mock the system table and run the tick under
  a desktop Lua to see the real error. Mine was forgetting cpu:reset().

Still open: sound, and the same recipe for the SM510 proper.

## Sound (2026-08-26, evening)

Ball now beeps. The chip side was already modeled (the game toggles R in
software; Ball's bursts measure 4- and 3-cycle half periods, 2048Hz and
~2731Hz squares); the game gates looped square samples on the per-frame
R edge count, picking the pitch from the toggle rate. Captured audio
puts the first blip at 5.72s after boot with a press at 5s, frame 343,
matching the desktop chip trace exactly, and the tap-vs-joypad audio
hashes are identical.

Two core findings that cost the evening hours:

- **`playsound` takes (sound, channel)**; channel -1 means pick one.
  The missing second argument raises a Lua error that kills the tick,
  and the core logs it through a callback most frontends discard, so it
  presents as a frozen game with no sound. The bench now has BENCH_LOG=1
  to surface core logs, and -a FILE to capture the audio stream raw.
- **The core never calls `rl_sound_init`**, so the mixer's active flag
  stays at its zero static initializer and rl_sound_mix returns silence
  forever. MADrigal games get sound only because compatinit runs
  `if not system.issoundactive() then system.resumesounds() end` every
  tick, which increments the flag. Any game bypassing compatinit must
  perform the same handshake.

## Real handheld artwork (2026-08-26, night)

Ball now wears its actual Nintendo shell. MAME external artwork packs
(the community's photographic scans) carry the full unit as a PNG plus
a default.lay that positions the emulated screen inside it. build_mgw.py
gained an artwork mode: pass the pack zip and the game's short name and
it uses the largest PNG (the scanned unit) as the panel, parses the .lay
for the screen window, fills that window with LCD grey-green so the dark
segments read, drops the segments in at LCD-window resolution, and lays
invisible tap zones over the real drawn buttons. Tapping the scanned
GAME A button is byte-identical to the joypad press, video and audio.

Notes for the other packs:

- The full-unit PNG is reliably the largest file in the pack. The .lay
  bounds are in view units; scale by (png_px / element_bounds) to reach
  panel pixels. The real screen window is the `<screen ... blend=
  "multiply">` bounds minus the unit element's bounds, times that scale.
- The .lay's `controls` group names the input mapping (inputtag/inputmask
  -> which pin) but its bounds are full-image press-animation overlays,
  not tight button rects, so button positions still come from locating
  them in the art. Colour-detecting the red buttons worked for Ball;
  ARTWORK_BUTTONS in build_mgw.py holds per-game rects.
- These packs are big (Ball's is 15MB unpacked, the unit PNG alone 5MB),
  so an artwork .mgw is ~1.6MB vs 25KB for the drawn-panel version. Fine
  for a handful; downscale the panel if packaging all 175.
- Artwork is credited (Ball: scan by Sean Riddle, unit by Ryan Holtz,
  case/screen art by Lee Robson, colour bg by Darth Marino). Any
  distribution must carry those credits; the packs are the artists' work.

31 relevant packs are staged in roms/artwork/ (gnw_ball plus the Tiger
and Konami handhelds, which wait on the SM510 core).
