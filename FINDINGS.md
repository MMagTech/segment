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

## SM510 sibling core, written ahead (2026-08-26, night)

`sm510/sm510.lua` is the SM510 core, ported from MAME source ahead of
having any SM510 ROM, to shorten the path once a Tiger or later-Nintendo
romset arrives. It is **UNVERIFIED** and says so at the top of the file:
the SM5A core earned trust by a lockstep trace against MAME, and this one
has not had that yet because no romset was available. The moment one is,
verify it the same way before relying on it.

What differs from the SM5A, all encoded and cross-checked against source:
base-class branch ops (T/TL/TML/TM) rather than TR/TRS; two stack levels;
SBM sets the RAM high bit for exactly the next instruction (via a one-step
bmask, set at the end of execute_one); the LCD is two 16-nibble RAM banks
(a at 0x60, b at 0x70) read column-by-column through get_lcd_row, not the
SM5A shift latches; and W is wired directly to S (sm510.h update_w_latch),
so WR/WS output the input strobe as they shift - there is no PTW in the
SM510 table.

What is checkable without a ROM passed: it loads, resets to the correct
3/7 vector, runs a full opcode spread for a second with no error, and the
LCD segment decode is exact - poking lcd_ram_a[3]=0b0101 lights 0.3.0 and
0.3.2 and nothing else. What is NOT yet checked: instruction-exactness,
timing, the bs segment, and the ROM image layout (assumed a flat 0x1000
image; confirm against the first real romset). SM511/SM512 melody is not
implemented; those add an internal melody ROM on top of this.

## SM510 core verified (2026-08-26, late) - ROMs arrived

A MAME 0.260 non-merged romset became available locally, so the
SM510 core got its real test. Verified against MAME 0.289 on gnw_stennis
(Snoopy Tennis, a plain SM510 game): lockstep PC/ACC/BL/BM/C trace
matches, rendered LCD identical to MAME's snapshot.

Verifying found one real bug. op_tl was dispatched only for op&0xfc ==
0x70, but MAME dispatches TL for 0x70, 0x74 AND 0x78 (the code comment
even said 70/74/78). A `TL $54C` at 0DE3 fell through and ran as a no-op,
splitting the trace at instruction 413. After the fix the trace matches
for thousands of instructions with only the divider-phase jitter the
SM5A core also has: a few TF1/TF4/TIS tests per 26k land on a divider-bit
transition a tick off MAME's timer phase. It is bounded (not growing),
the game's timing loops absorb it, and the display is unaffected - the
same "fraction of a cycle" imprecision noted for the SM5A.

The SM510 ROM (sp-30) is a flat 0x1000 image, confirming the loader
assumption. 162 of the 175 driver romsets are now local under roms/mame/
(the 13 absent were added to MAME after 0.260). Remaining: SM511/SM512
melody (not implemented; needed for the Tiger games that have music), and
the S-strobe K-input muxing for building SM510 games into .mgw (the CPU
exposes write_s/read_k, but which S bit gates which K column is per-game
driver logic to replicate in the game.lua template).

## The artwork loader generalized across all 157 packs (2026-08-27)

`build_mgw.py`'s artwork mode worked for one pack. It found the panel by
taking the largest PNG in the zip and the LCD window by regexing the
first `<screen blend="multiply">` bounds, which happened to be right for
gnw_ball and silently wrong elsewhere: on trthuball the largest file is
the printed LCD background, not the unit, so the window landed nowhere
near the screen. That is now `tools/gw/artwork.py`, which renders any
pack down to one panel and its screen rectangle.

The packs vary far more than "unit PNG plus screen bounds" suggests. The
survey across all 157: up to 20 views each, named anything from
`Unit Only` to `External Layout`; 1055 image elements, 36 `rect` and 2
`disk`; alpha, multiply and add blends; `element`, `overlay`, `bezel`,
`collection` and `group` containers; both `x/y/width/height` and
`left/top/right/bottom` bounds; 33 JPEGs among the PNGs; and 17 units
with two screens (one with three). No amount of per-pack special-casing
was going to hold, so nothing in artwork.py keys off a pack's shape.

What it does instead:

- **Pick the view by name, then by geometry.** `Unit Only` beats
  `Unit and Backdrop` beats a bare `Handheld` view beats
  `Background Only`; partial-zoom and fan-art variants are demoted. A
  view is only a candidate if it has both a screen and a unit.
- **Flatten the view** to a draw list in document order, expanding
  groups and collections. An element whose only component carries a
  `state` other than the element's `defstate` is a button
  press-animation and is dropped: those are laid over the whole unit and
  otherwise tie with it on bounds, which is how the first attempt picked
  `Grey-Flat-3` as gnw_ball's panel.
- **The unit is the largest image that still contains the screen**, with
  a room backdrop dropped when it is laid down first, spans the whole
  view, and something else is left. Largest, not smallest: the printed
  LCD background sits inside the unit and contains the screen too, which
  is what made tmegaman3 render as a bare grey rectangle. Ties go to the
  later element, so a unit wins over its own drop shadow.
- **Its bounds are the panel frame.** That is what crops the room away
  without needing to know a room is present, and it is why a scene view
  like gnw_ball's yields the console rather than a console-in-a-room.
- **Composite everything into that frame** at the unit's own resolution,
  honouring alpha and multiply. Containment is judged with a tolerance
  of 3% of the screen, because artists let a screen overhang its
  background by a few units (trtreisl by 4).

The screen is not painted. It is left as the artwork renders it, so a
printed LCD background survives, and only filled with LCD grey-green
when it comes out empty or dark, meaning the pack expected the emulator
to supply every lit pixel. That flat fill used to be unconditional, and
it was erasing real art: Snoopy Tennis's tree, doghouse and grass are
printed on the LCD, not drawn by the chip.

Verification, since "it renders" is not the same as "the window is
right": every romset carries its own SVG, whose aspect ratio must match
the screen rectangle the .lay describes. Across the 148 packs with a
local romset, **none is more than 12% off** and 113 are within 3%; the
residue is artist slop in hand-placed bounds. All 157 packs render. The
18 still flagged are the 17 multi-screen units and one pack (vinnpukh)
whose only view is `Background Only`, both correctly reported rather
than silently mis-packaged.

Regression evidence: Ball's generated `game.lua` is byte-identical to
the committed build's, so the new path reproduces the old geometry
exactly, and a joypad Game A press and a tap on the drawn GAME A button
still produce identical video and audio hashes. Only `background.rle`
changed, because the LCD window now carries the scanned LCD surface
instead of flat grey.

Snoopy Tennis built with artwork proves the SM510 path takes it too:
1741x1047, same audio hashes as the drawn-panel build in both idle and
Game A runs, so only the panel differs.

Notes for what comes next:

- Tap zones now live in the .lay's own view coordinates and are mapped
  through `Panel.to_panel_rect`, so they survive any panel resolution.
  `MAX_PANEL` caps the panel; native resolution is kept below it, which
  is how Ball still comes out at 2227x1499.
- **The .lay cannot give button positions.** 117 packs carry no input
  tags at all, and of the 39 that do, 38 have exactly two distinct
  rectangles: the full-unit press overlays, once per view. Button rects
  have to come from the art itself.
- **The .lay can give the input map.** For gnw_stennis the tagged
  elements read IN.0 0x08/0x02/0x01 and IN.1 0x04/0x02/0x01, which is
  exactly the hand-derived `INPUT_MAPS_SM510['gnw_stennis']`, with names
  recoverable from the element refs (`Hit-Flat`, `Up-Flat`, `Down-Flat`).
  For the other 117 it has to come from the driver's INPUT_PORTS.
- artwork.py needs Pillow and numpy. The hand-rolled `read_png` in
  svg2segs.py cannot open the packs' JPEGs, and compositing a dozen
  multi-megapixel layers in pure Python is not worth the purity.

## 25 SM5A games built in a batch (2026-08-27)

The pipeline now runs unattended. 25 SM5A handhelds build from romset
plus artwork pack and all 25 boot, respond to Game A and drive the
buzzer, verified headless. Twenty-four of them had never been packaged
before. Evidence: sm510/evidence/sm5a_batch_25_playing.jpg, one live
frame per game.

Three things had to be true first, and only one of them was.

**The plan's "simplest case, no muxing" was wrong.** It assumed the SM5A
games read K directly the way Ball does. Only gnw_ball, gnw_fires and
gnw_vermin do. Everywhere else MAME wires `piezo_input_w`, which splits
the R output in two: bit 0 drives the piezo, bits 1 and up drive the
input mux, and K comes back as the OR of the selected columns plus a
fixed column if the game called `inp_fixed_last()`. Ball works without
any of that because it has one column and never looks at the mux. So the
SM5A template needed the same strobe muxing the SM510 one already had,
driven from R rather than S. The core needed no change: sm5a.lua already
hands the game every R write, which is how the sound gating works.

One consequence worth remembering: the buzzer must count transitions of
R bit 0 alone, not every write_r call. Counting calls was correct while R
did nothing but beep; once R also scans the keyboard, the scan traffic
would be heard as a tone.

**The wiring is fully extractable, so no game needs hand data.**
`tools/gw/extract_inputs.py` reads INPUT_PORTS, `inp_fixed_last()` and
the machine config out of hh_sm510.cpp and emits tools/gw/inputs.json
for all 169 games. It reproduces both hand-written maps exactly: the
gnw_stennis entry equals INPUT_MAPS_SM510's, and gnw_ball's K bits equal
what Ball's game.lua had hardcoded. It also caught something a hand copy
would not have: the BA and B test pins are ACTIVE_LOW on Ball and
ACTIVE_HIGH on Octopus, so assuming Ball's polarity would have left
Octopus with both directions permanently held.

**svg2segs only understood one of the two ways a romset marks
segments.** It looked for the innermost `<g>` carrying an `o.y.h` title.
Chef marks 61 of its 72 segments on the `<path>` itself and only 11 in
groups, so Chef built with 11 segments and looked empty. The scan now
accepts any drawable tag and requires the block to carry exactly one
segment title, which is what separates a segment from a container that
happens to hold segments. Ball's 68 output files come out byte-identical
afterwards, so it is a strict generalization. Chef was the only romset
of the 24 affected, but the count check that found it (titles in the SVG
against segments in the manifest) is worth running over any new batch.

Verification per game: boot, then compare a 900-frame idle run against
one with Game A held, on video and audio hashes separately. All 25
differ on video, 23 on audio. The two that do not are Space Mission and
Spider, both Tronica shooters whose only other control is left/right, so
silence through a run that presses nothing but Game A is expected rather
than a fault; both use the same sm5a_common sound wiring as the games
that do beep.

Cost: about ten seconds per game end to end, panel compositing included.

## English is the default (2026-08-27)

A standing rule from MMagTech: never build a foreign-language unit when
the same game is available in English, which is not a rule against
foreign-language units. Where no English version exists, build it.

Applied to this corpus the rule currently excludes nothing, and the
reason is worth recording before someone applies it more aggressively.
Of the 144 romsets with both a ROM and an artwork pack on disk, 135 are
English (Nintendo, Tiger, Konami, Tronica) and 9 are Cyrillic
(Elektronika). MAME files eight of the nine as clones of gnw_mmouse or
gnw_octopus, but clone there means "runs the same chip program", not
"is the same game": the LCD art differs completely, so Hockey, Biathlon
and Ataka asteroidov look nothing like Mickey Mouse and have no English
edition. Checked directly, the 11 titles held by more than one romset
are all Nintendo hardware variants (Wide Screen against Panorama, Silver
against Gold, CN-07 against CN-17); not one pair differs by language.

## Button rectangles, taken from the packs themselves (2026-08-27)

The earlier note said the .lay cannot give button positions, and that
image analysis on the panel would be needed. Both halves were wrong in a
useful way. The .lay's control group really does carry nothing but
full-unit bounds, but the images it references are exactly what is
needed: each is a full-size overlay drawing one button in its pressed
state, and the element that references it carries the inputtag and
inputmask naming the input that button reports. Composite one over the
idle panel, take the bounding box of what changed, and that is the
button, with its identity attached. No colour heuristics, no hand
measuring.

It lands where a person would put it. Against the five rectangles
measured by hand for Ball:

    LEFT    auto (205, 994,170,160)   hand ( 212, 995,165,130)
    RIGHT   auto (1852, 994,171,165)  hand (1850, 990,175,135)
    GAME A  auto (1089,1267,124, 72)  hand (1085,1250,140,100)
    GAME B  auto (1325,1267,124, 72)  hand (1320,1250,140,100)
    TIME    auto (1561,1267,124, 70)  hand (1550,1250,140,100)

and the proof that matters is behavioural: a tap at the centre of the
auto-detected GAME A produces output byte-identical to a joypad Game A,
and the same holds for RIGHT, which goes through the BA pin rather than
a K column. The change region runs a little wider than the button
because the artist drew its shadow moving too, which only makes for a
fairer touch target. Overlaps are shrunk about their centres rather than
dropped; on this corpus nothing has overlapped yet.

Two things had to be understood first.

**defstate does not decide what an input-bound element draws.** MAME
drives those from the input, so on an idle panel every one of them is
unpressed regardless of what defstate says. Reading defstate put every
button of a `defstate="1"` pack onto the panel already pressed, and left
no press image to locate it by, which is why gnw_manholeg and trspacmis
first reported no buttons at all while plainly shipping the images. The
compositor now asks for state 0 whenever an element is wired to an
input, and the button finder asks for the highest non-zero state.

**A layout's port numbering can be stale.** gnw_helmet's .lay says its
Game A button reports IN.0 bit 2, but the driver has since moved those
inputs to IN.2, so three of its five buttons resolved to nothing. The
file names did not drift, and the artwork community names them
consistently (Left-Flat, Right-Flat, Grey-Flat-1/2/3 for Game A, Game B,
Time), so the name is the fallback when the tag does not resolve, and
only ever to an action the game actually has.

Coverage: 15 of the 27 SM5A games ship press images and get tap zones.
The rest are joypad-only and say so at build time. trspacmis is a
partial case worth knowing about: it ships press images for its two
arrow buttons but not for Game A, Game B or Time, so those three stay
untappable even though the pack has artwork for them.

## The 384k save-under budget, and how it presented three ways (2026-08-27)

The first full 88-game build had 38 Tiger games die on load with SIGBUS
and two Nintendo units (gnw_dkjr, gnw_mbaway's sibling gnw_mariocm) boot
into a display no input could change. Those looked like three bugs, an
input bug, a load crash and a freeze, and they are one.

retroluxury saves the pixels under every visible sprite so it can
restore them next frame, into a fixed static buffer of 384k pixels
(RL_BG_SAVE_SIZE) with no bounds check. rl_image_blit just walks past
the end. A unit whose lit segments together exceed the budget corrupts
whatever sits after the buffer:

- Exceed it hugely (a Tiger unit lighting its whole 2560-wide overlay
  at power-on, megapixels of segments) and the process dies with SIGBUS
  during the first frame, which the sweep reports as LOAD-FAIL.
- Exceed it mildly (gnw_dkjr, 494k) and it survives, but the restore is
  garbage: the all-on power-on frame bakes into the background
  framebuffer permanently. From then on the sprites' visibility changes
  are invisible, so the game looks frozen and dead to input while the
  chip underneath runs and responds perfectly.

The diagnostic rabbit hole is worth recording because the freeze
mimics an input bug exactly. The chip traced correct against MAME
(divider-phase jitter aside), the game logic ran clean under a mocked
desktop Lua with inputs working, the layout constants were right, and
the core demonstrably delivered inputs to a sibling game. The tell that
ended it: a build patched to unconditionally hide every sprite each
tick still showed all segments on screen, while the packaged background
decoded clean. Pixels that no sprite explains and no background
contains are baked save-under corruption.

The fix is at build time, since games must run on the stock core:
build_mgw sums each segment's opaque pixels (the rle 'used' field) as
it encodes, and if the worst case, every segment lit at once, exceeds
92% of the budget, it reports the required rescale and exits; the batch
then re-renders panel and segments at sqrt(budget/total) of the size
and retries. Tiger units land around 900px wide, well past legible.
Worst in the corpus is kdribble (Double Dribble), whose triple-overlay
segments total 7.6M px and force 480px; it had loaded fine before only
because its boot lights few segments, and it was a guaranteed mid-game
crash on the stock core.

Separately, the Tiger games that did load ignored their start button:
they wire it as IPT_START ("Power On/Start"), which the extractor
didn't map, so nothing pressed it. IPT_START now maps to a 'start'
action bound to the retropad's own start button.

## Dual-screen units (2026-08-27, late)

The nine two-screen SM510 units build and play: Donkey Kong, Donkey
Kong II, Green House, Life Boat, Mario Bros., Mickey & Donald, Oil
Panic, Rain Shower, Squish. All nine boot and respond in the sweep.
Evidence: sm510/evidence/mgw_dkong_dual_playing.png, Donkey Kong
mid-game on both screens.

It cost very little, which is the payoff of the earlier generality:
artwork.py already reported every screen's rectangle (Panel.lcds), and
one chip drives both LCDs, so no core work at all. The changes are
plumbing: build_mgw takes comma-separated segment dirs (one per screen,
top or left first), segdefs carry a screen index, the game template
places each segment against its screen's origin from an LCDS table, and
the batch extracts each romset's two SVGs (named _top/_bottom or
_left/_right) and renders each at its own window's width. The two SVGs
share the chip's o.y.h tag namespace, which is correct, not a
collision: a tag appearing in both would simply light in both, exactly
as the shared chip output would. Single-screen games rebuilt through
the new path are byte-identical.

Two of the nine (gnw_dkong2, gnw_mickdon) have heavily overlapping
soft-edged segment art and rescaled to ~330-420px wide to fit the
RL_BG_SAVE_SIZE budget, the same story as tbatfor/tjdredd/trockteer.
More weight for the upstream filing.

The multi-screen packs ship no press images, so all nine are
joypad-only until the position-based tap zones land.

## SM511/SM512 melody core (2026-08-27, night)

`sm510/sm511.lua` is the melody sibling, ported from MAME's sm511.cpp
(fetched into ref/, it was not in the original reference set). Verified
the same way the other two cores were, and it passed on the first
attempt: a lockstep PC/ACC/BL/BM/C trace against MAME 0.289 on
gnw_climber is identical for all 18,137 instructions MAME executes in
3 emulated seconds, with no divergence regions at all, and the 6-second
segment set pushed through the romset's SVG matches MAME's snapshot dot
for dot (sm510/evidence/sm511_climber_vs_mame.png). The first-try clean
trace after two hard-won cores says the porting discipline transfers.

What the SM511 actually is, having read it: not an opcode superset of
the SM510 but a rearrangement. KTA moves to 0x50, ROT to 0x00; TF1, TF4
and ATR do not exist; TL owns the whole 0x70 row and TML sits at
0x68-0x6b; a 0x60-prefixed two-byte page carries RME/SME/TMEL/ATFC/BDC/
ATBP plus CLKHI/CLKLO, which switch the instruction clock between 16kHz
and 8kHz, and the chip boots at 8kHz (clk_div 4), so the divider-per-
instruction ratio is dynamic, exactly the thing that was nearly
misdiagnosed on the SM510 batch. W shifts silently and PTW outputs the
latch. The R pin belongs entirely to the melody controller: a 256-byte
melody ROM of note commands stepped on divider F7, synthesized through
the datasheet's tone-cycle table.

Sound in the packaged games does not count R edges (a melody would
average into nonsense); the core exposes melody_hz(), the frequency of
the note currently under the melody pointer, and the game shell keeps a
bank of looped squares (12 notes x 2 octaves, 24 files, generated by
build_mgw from the same tone table) and gates the nearest one. Climber
plays with sound end to end on the stock core; idle and Game A runs
differ in both video and audio hashes.

The batch learned to split the two binaries a melody romset carries
(the 4K .program and the 256-byte .melody); picking the first file by
sort order would have loaded the melody ROM as the program.

## The melody batch: the catalogue is effectively built (2026-08-27, night)

39 SM511/SM512 games attempted, 39 built, 38 boot and respond,
including Zelda (SM512, dual-screen, the marquee of the whole family),
Super Mario Bros. with taps verified byte-identical to the pad, Balloon
Fight, and the entire Konami handheld line (all four TMNT titles, Top
Gun, Gradius, Contra). The one non-responder is bassmate, the Telko
Bassmate Computer, a fishing calculator rather than a game: it boots
and displays but ignores every input, joining tgaiden and tvindictr on
the open list.

Two mechanical finds: a maker string containing a slash ("Telko /
Nintendo") became a directory in the output filename, so batch
filenames are now sanitized; and the earlier melody-romset split (the
4K .program vs the 256-byte .melody) held up across all 39.

games-out/ now holds 136 games. This is every game in MAME's hh_sm510
driver that has a romset and an artwork pack on disk and a verified
chip, minus nothing: the single-screen batch, the dual-screen nine, and
the melody catalogue are all in. What remains is quality (tap zones for
the ~85 games whose packs ship no press images, the three input
mysteries, the five budget-shrunk games) and reach (the 14 rom-no-art
games buildable with drawn panels, the 8 art-no-rom games waiting on
dumps, the 8 games on unported chips like Konami's SM530 trio).

## Drawn panels, redesigned and shipped (2026-08-27, late night)

The no-artwork fallback was rebuilt from the ground up after its first
public screenshot: the old panel hardcoded Ball's five buttons at
Ball's proportions, clipping labels on any other screen shape and
giving every game LEFT/RIGHT whether it had them or not. panel_layout()
now derives the controls from the game's own extracted wiring, so
Nu, pogodi! gets its real paired UP/DOWN buttons per side, Space Rescue
gets its single LEFT/RIGHT pair, and the pill row sits labelled and
centred under the LCD at any aspect. Every drawn control doubles as a
tap zone.

Nine of the fourteen no-artwork games are built and installed:
gnw_helmeto, gnw_judgeo, gnw_mariocmta, nummunch, nupogodi, rkosmosa,
tigarden, trshutvoy, trsrescue. Six respond with taps byte-identical to
the pad. The three that boot but ignore the sweep (nummunch, tigarden,
trshutvoy) are keypad devices: their INPUT_PORTS carry IPT_KEYPAD
banks, which the extractor records as a 'keypad' action nothing maps
yet. Numeric keypads need their own tap-zone treatment (a drawn keypad
grid) and are a follow-on item. Files are tiny: 40-100KB against the
1-2MB artwork editions.

Still parked: the three Nelsonic watches (SM530, unported chip) and
kosmicmt/vespovar (machine config not yet classified).

## Converter, coverage, SM530, and the repro draft (2026-08-27, deep night)

The pipeline is now one command (tools/gw/convert.py): romset in,
verified .mgw out, narrating each step and ending in a headless
boot-and-respond self-test that refuses to emit a non-responding game.
It reproduces the batch builds and cleanly rejects unknown romsets and
unported chips. tools/gw/coverage.py generates COVERAGE.md, the standing
backlog: what is built, and for the rest, whether the blocker is
artwork, a ROM dump, or a chip port. The drawn-panel fallback was
rebuilt to derive its control layout from each game's own wiring, so
every no-artwork game is playable and correctly laid out today rather
than clipped.

extract_inputs.py now classifies every game in the driver (the last
holdouts were a spaced ampersand in the machine-config signature, the
tiger2bit/tiger1bit melody variants, and games whose config just
delegates to another via a bare `foo(config)` call).

sm530.lua ports MAME's sm530.cpp for the Nelsonic/Konami watch chip:
the fully rearranged opcode map, the 8-bit K read as two nibbles
(KTA/KETA), the four-flag gamma (10s/1s/0.5s/0.1s) tested per-bit by
TG, the 1s and 1/100s hardware counters, the display-enable latch, the
F output, and the SM511 melody controller at a 0xff step mask. Written
to the same recipe as the three verified cores but NOT yet
lockstep-verified: the three SM530 romsets' vector SVGs postdate the
local MAME 0.260 set, so `mame nstarfox` cannot render headless here to
trace against. Marked unverified until a newer romset or a hand-built
oracle is available.

Upstream repro, drafted not verified: upstream/make_overflow_repro.py
builds a synthetic .mgw (no ROM, no artwork, just N opaque sprites over
a plain canvas) intended to overrun RL_BG_SAVE_SIZE for the bug report.
It does not crash under the headless bench, which appears not to drive
rl_sprites_blit the way RetroArch's compositor does (the games that
crash do so in the real frontend). The generator is kept for the filing
session, where it needs tuning against a live RetroArch build; the bug
itself is real and reproduced by the actual Tiger/dual-screen games,
which is what the pixel-budget guard exists for.

## The "input mysteries" were a sweep artifact (2026-08-27, resolved)

tgaiden (Ninja Gaiden) and tvindictr (Vindicators) were flagged as
booting but ignoring input. They do not: Ninja Gaiden responds to
Select, Vindicators to six different buttons. Neither has a "Game A",
and the verification sweep only pressed Game A/Start (retropad id 3), so
it declared no-response for any game that wakes on a different button.
The sweep now tries Game A, Select/Pause, L1 and A in turn before
reporting a failure. Nothing was wrong with those two builds.

bassmate (Bassmate Computer) genuinely responds to nothing through the
pad: it is a fishing calculator whose inputs are a 12-key keypad matrix
with no retropad equivalent. It is not a mystery either, just the same
keypad-device case as nummunch/tigarden/trshutvoy, which need a drawn
keypad grid to be usable.

## Automatic tap zones without press images: not reliably solvable (2026-08-27)

The ~100 games whose artwork packs ship no pressed-state overlays cannot
have their buttons located the clean way (composite-the-press-image).
Three detection approaches on the composited panel were tried and none
is shippable:

1. Local contrast vs a blurred background: finds small pill buttons,
   misses large smooth round ones (the blur tracks them).
2. Distance from the case's dominant colour: works on plain cases,
   returns nothing on units with bold coloured frames (the border
   dominates the statistics).
3. Region-constrained blobs (search only the strips outside the LCD,
   inset past the frame, cross-check count vs wiring): best of the
   three, and genuinely good on some units (gnw_dkong finds the d-pad
   and JUMP), but noisy on busy cases (16 false hits on ktmnt2's green
   shell) and partial on most. Evidence:
   sm510/evidence/tapzone_detection_attempt.jpg.

The blocker is not just detection accuracy: there is no automatic ground
truth. With press images, the element's inputtag/inputmask names which
action a located button drives, and a tap can be proven byte-identical
to that button's pad press. Without them, even a perfectly located blob
has no verified mapping to an action, and a mis-assigned tap zone is
worse than none. So this stays manual/deferred rather than shipping
guessed zones. The games are all fully controller-playable meanwhile,
and any pack that later gains press images upgrades automatically
through the existing path.
