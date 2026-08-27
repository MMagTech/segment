# Start here (next session)

The foundation is complete and committed. Both chip cores (SM5A, SM510)
are ported from MAME and verified against it; Ball (SM5A) plays with real
artwork + sound; Snoopy Tennis (SM510) plays. Pipeline builds both chips
into playable .mgw on the stock gw-libretro core. See FINDINGS.md for the
full technical record.

## Assets on disk (gitignored — your own files)
- `roms/mame/` — 162 of 175 driver romsets (MAME 0.260 non-merged)
- `roms/artwork/` — 157 of 175 console artwork packs (1.6G)
- Both were copied from the NAS / Drive archives; ROMs and artwork are
  never committed (tool-not-payload; see README).

## The next task: automation push, in this order

1. **Generalize `tools/gw/build_mgw.py`'s `load_artwork()` across .lay
   forms.** It handles Ball's "unit element + screen bounds" form but
   returns wrong LCD-window bounds on others. Known forms seen:
   - Ball / trthuball: `<screen blend="multiply">` bounds inside a unit
     element (but trthuball's numbers came out wrong — debug the element
     matching + scale).
   - kdribble: "Backdrop, Overlay" — screen fills the whole view, a `bg`
     overlay (transparent window) on top. Different math.
   Make it robust, then...

2. **Batch-build the ~17 SM5A games that have real art** (simplest case:
   direct K input, no muxing, no melody). This proves the pipeline
   generalizes beyond n=2 — the whole point. List: run
   `python3 - <<'PY'` cross-referencing roms/artwork ∩ roms/mame with
   sm5a_common in sm510/ref/hh_sm510.cpp (see the classify snippet in
   the 2026-08-27 session, or FINDINGS).

3. **SM510 input maps + button positions, auto-extracted** from each
   game's INPUT_PORTS in the driver, so it scales without hand data.
   Currently only gnw_stennis + trthuball input maps and only gnw_ball
   ARTWORK_BUTTONS exist.

4. **SM511/SM512 melody core** — ~40 Tiger/Konami games have music.

5. **`convert.py` wrapper** (one command: romset.zip + artwork.zip ->
   .mgw) — LAST, once the pipeline is proven across many games.

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

## Separately: upstream
- Issue #84 / PR #85 (compatinit pointer fix for the 59 existing games)
  is filed and awaiting maintainer response. Nothing to do until then.
