# Start here (next session)

Both chip cores (SM5A, SM510) are ported from MAME and verified against
it; Ball (SM5A) and Snoopy Tennis (SM510) both play with real artwork
and sound. The artwork loader now generalizes across the whole pack
corpus, so the pipeline is no longer tied to hand-tuned per-game data
for the panel. See FINDINGS.md for the full technical record.

## Assets on disk (gitignored, your own files)
- `roms/mame/`: 162 of 175 driver romsets (MAME 0.260 non-merged)
- `roms/artwork/`: 157 of 175 console artwork packs (1.6G)
- Local copies of your own files; ROMs and artwork are never
  committed (tool-not-payload; see README).

## Done: artwork generalization (2026-08-27)

`tools/gw/artwork.py` renders any MAME external artwork pack down to one
panel plus its screen rectangle. All 157 packs render; cross-checked
against each romset's own SVG aspect ratio, none of the 148 checkable
packs is more than 12% off. Ball's generated `game.lua` is byte-identical
to the previous build, so the geometry is a strict generalization, not a
change. Details and the shape of the corpus are in FINDINGS.md.

## Standing rule: English is the default

Never build a foreign-language unit when the same game is available in
English. This does not mean avoiding foreign-language units: where no
English version of that game exists, build it.

As the corpus stands the rule excludes nothing, and it is worth knowing
why before someone applies it more aggressively. Of the 144 romsets with
both a ROM and an artwork pack on disk, 135 are English (Nintendo,
Tiger, Konami, Tronica) and 9 are Cyrillic (Elektronika). MAME files
eight of the nine as clones of gnw_mmouse or gnw_octopus, but they are
not translations: they run the same chip program with entirely different
LCD art, so Hockey, Biathlon and Ataka asteroidov look nothing like
Mickey Mouse. Each is its own product with no English edition.

Checked directly: 11 titles in the corpus are held by more than one
romset, and every one of them is Nintendo against Nintendo, a hardware
variant such as Wide Screen against Panorama or Silver against Gold.
Not one pair differs by language. So no game currently has both an
English and a foreign edition to choose between.

## The next task: automation push, in this order

1. ~~Batch-build the single-screen games.~~ **Done 2026-08-27, both
   chips: 88 of 88 single-screen SM5A+SM510 games build, 86 boot
   and respond, every game with tap zones has taps
   byte-identical to the pad.** The batch survived two systemic finds,
   both in FINDINGS: the stock core's 384k-pixel sprite save-under
   budget (build now measures worst case and rescales the panel to
   fit), and Tiger's IPT_START wiring. Known issues, all Tiger:
   - tgaiden, tvindictr: RESOLVED, not bugs. They respond fine (to
     Select / other buttons); the old sweep only tried Game A. bassmate
     is a keypad calculator, needs the keypad grid.
   - tbatfor, tjdredd, trockteer: their segments stack ~4 layers of
     75%-alpha artwork over the whole screen, so fitting the core's
     budget forces the panel down to ~240px wide. Built and responding,
     but visibly low-resolution; the honest fix is a bounds check (or a
     bigger RL_BG_SAVE_SIZE) upstream, worth raising alongside PR #85.
   - trobhood: ports used PORT_INCLUDE; the extractor resolves it now,
     and Robin Hood responds.

2. ~~Button rectangles from the art.~~ **Mostly done 2026-08-27.**
   The packs carry the answer: each ships a full-size overlay drawing one
   button pressed, tagged with the input it reports, so compositing it
   over the idle panel and taking the bounding box of the change locates
   the button and names it. artwork.buttons() does this; all 15 SM5A
   games whose pack ships those images have every tap verified identical
   to the pad. See FINDINGS.md.

   Still open: the 12 SM5A packs that ship no press images (9 Elektronika,
   2 Tronica, plus trspacmis which has them for its two arrows only).
   Two detector attempts on the composited panel both fell short and are
   worth knowing about before a third:
   - Local contrast against a blurred background finds the small pill
     buttons and the case lettering, and misses the large smooth round
     buttons, because the blur follows them.
   - Distance from the case's dominant colour finds buttons on the
     plain-cased units but returns nothing on ehockey and trsgkeep,
     whose bold coloured borders dominate the statistics.
   A third angle worth trying: these units place their controls in
   predictable regions relative to the LCD window (a pair left, a pair
   right, a row or column of small ones). Generous zones by region,
   assigned by position and then confirmed with the tap-equals-pad
   check, would be playable even if not pixel-tight, and a wrong guess
   fails the check rather than shipping.

3. **Input maps, auto-extracted.** Two sources: the .lay's
   `inputtag`/`inputmask` attributes where present (verified to
   reproduce the hand-written `INPUT_MAPS_SM510['gnw_stennis']` exactly,
   with action names recoverable from element refs like `Hit-Flat`), and
   each game's INPUT_PORTS in the driver for the rest.

4. ~~Multi-screen units.~~ **Done for SM510, 2026-08-27: all nine
   two-screen units build, boot and respond** (Donkey Kong, DK II,
   Green House, Life Boat, Mario Bros., Mickey & Donald, Oil Panic,
   Rain Shower, Squish). The remaining multi-screen games (gnw_zelda,
   gnw_dkjrp and friends) are SM511/SM512 and wait on the melody core.
   dkong2/mickdon rescaled small under the pixel budget; see the
   upstream filing note.

5. ~~SM511/SM512 melody core~~ **Done 2026-08-27: core verified
   lockstep against MAME first try (18,137/18,137 instructions on
   gnw_climber, display dot-for-dot); 39 games built, 38 respond,
   including Zelda and the Konami line. games-out/ holds 136.**
   Known non-responders: tgaiden, tvindictr, bassmate.

6. **`convert.py` wrapper** (one command: romset.zip + artwork.zip ->
   .mgw), LAST, once the pipeline is proven across many games. Note it
   must run artwork.render() first to learn the LCD window size, then
   svg2segs.py at that width, then build_mgw.py; build_mgw warns if the
   segments were rendered at the wrong width.

## Gotchas already learned (all in FINDINGS.md)
- Divider-phase jitter in both cores is benign for display but watch for
  a game whose timing genuinely depends on exact TF1/TF4/TIS phase.
- gw core never calls rl_sound_init: games must run
  issoundactive/resumesounds each tick or the mixer stays muted.
- system.playsound needs (sound, channel); channel -1 = pick free.
- After setbackground, set the panel image's picture=nil or a full-panel
  sprite overflows the save buffer (SIGBUS).
- The gw-libretro Makefile misses functions.o's dep on the baked-in Lua
  headers: `touch gwlua/functions.c` after regenerating any .h.
- Games built here run on the STOCK core; they don't need PR #85 (that
  fix is only for the 59 MADrigal compat-path games).
- The bench's `-tap` needs port 2 (`-tap frame,x,y,hold,2`); port 0 is
  the joypad's and answers nothing. And zsh does not word-split an
  unquoted `$var`, so building bench arguments in a shell variable
  silently passes them as one argument and every run looks identical.

## Separately: upstream
- Issue #84 / PR #85 (compatinit pointer fix for the 59 existing games)
  is filed and awaiting maintainer response. Nothing to do until then.
- TO FILE (decided 2026-08-27): the RL_BG_SAVE_SIZE overrun.
  rl_image_blit writes past the fixed 384k-pixel save-under buffer with
  no bounds check; slight overrun silently bakes sprites into the
  framebuffer, large overrun is SIGBUS. File it as a memory-safety bug
  with a SYNTHETIC repro .mgw (a plain Lua unit creating a few hundred
  large solid sprites - no ROM, no artwork, freely attachable), the
  offending line, and a two-line fix (bounds-check and clamp, or grow
  the buffer). Motivation gets one sentence. User direction 2026-08-27: the issue
  links the public repo, and the README must serve two readers by then,
  a semi-technical account and plain instructions for someone who just
  wants to build games, so issue readers can pick up the tool alongside
  the synthetic repro if they choose. Sequence: converter first (so the
  instructions are one command), README second, filing draft third.
