# DRAFT issue for libretro/gw-libretro — file when ready

Status: draft. **Before filing, confirm the synthetic repro actually
crashes on a real RetroArch build** (it does not trigger under a
headless harness, which drives a different video path than the
frontend compositor). Tune `upstream/make_overflow_repro.py`'s NSPRITES
until a stock RetroArch build reproduces, then attach that .mgw.

---

**Title:** rl_image_blit overruns the fixed RL_BG_SAVE_SIZE buffer;
framebuffer corruption then crash with many/large visible sprites

**Body:**

`rl_sprites_blit` in retroluxury saves the pixels under every visible
sprite into a fixed static buffer so it can restore them next frame:

```c
static uint16_t saved_backgrnd[ RL_BG_SAVE_SIZE ];   // 384 * 1024
...
saved_ptr = saved_backgrnd;
do {
  sptptr->bg = saved_ptr;
  saved_ptr = rl_image_blit( sptptr->sprite->image, ..., saved_ptr );
  sptptr++;
} while ( sptptr->sprite->flags == 0 );
```

`rl_image_blit` advances `saved_ptr` by the number of pixels it copies,
with no check against the end of `saved_backgrnd`. When the visible
sprites' covered pixels sum past 384k, the writes run past the buffer:

- a modest overrun silently corrupts whatever memory follows (in
  practice the game's own state / framebuffer, so the display bakes in
  and stops updating);
- a large overrun segfaults.

This is reachable from ordinary content, not just a synthetic case: a
unit that lights a large LCD with many segment sprites at once exceeds
the budget. (Context: found while packaging chip-accurate handheld
emulations as .mgw for this core; games with big or busy LCDs overflow.
More on that below, but the bug stands on its own as an unchecked
buffer write.)

**Repro:** attached `rl_bg_save_overflow.mgw` contains no game ROM and
no artwork — just N opaque sprites over a plain canvas, all made
visible, so the saved-pixel total exceeds 384k. Load it in the Handheld
Electronic Game core. (Generator:
`upstream/make_overflow_repro.py`, so you can regenerate at any N.)

**Fix:** bounds-check the accumulation and stop saving when the buffer
is full (clamp, skip save-under for the overflowing sprites), or size
`RL_BG_SAVE_SIZE` to the framebuffer. Either removes the out-of-bounds
write; a clamp keeps memory bounded regardless of content.

---

**Optional context to include (the user may add or drop this):**

The overflow surfaced while building a toolchain that packages MAME's
chip-level handheld emulations (Game & Watch, Tiger, Konami, etc.) as
self-contained .mgw for this core — the chip emulator ships inside each
game, so they run on the stock core. Tools and details:
https://github.com/MMagTech/segment . Anyone wanting to reproduce with
real content, or to try the games, can build one with a single command
(`tools/gw/convert.py <romset.zip> [artwork.zip]`); the repository's
COVERAGE.md lists what already builds. The buffer limit currently forces
a handful of the most segment-dense units to render at reduced size to
stay under budget; a bounds check or a larger buffer upstream would let
them render full-size.
