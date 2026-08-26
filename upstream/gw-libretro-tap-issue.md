# Pointer/touch input is unreachable for every shipped game (1.6.0 feature is dead code)

## Summary

The 1.6.0 changelog says:

> Added support for mouse and touch screens.
> * All buttons can be activated with clicks and taps

This does not hold for any of the 59 published games. The pointer
handling exists and looks correct, but it lives on an init path that no
shipped game reaches, so every click and tap is discarded.

## The cause

`system.lua` dispatches on the type of its first argument:

```lua
-- gwlua/lua/system.lua:494
M.init = function( background, keymap, keydown, keyup, timers, zoom, options )
  if type( background ) ~= 'table' then
    -- Use the compatibility init function
    local compatinit = M.loadunit( 'compatinit' )
```

A config table takes the modern path, whose per-frame closure reads
`pointer_pressed` / `pointer_x` / `pointer_y` and hit-tests the press
against the registered controls. Anything else is handed to `compatinit`.

`compatinit.lua` contains no reference to `pointer` at all:

```
$ grep -c pointer gwlua/lua/system.lua gwlua/lua/compatinit.lua
gwlua/lua/system.lua:8
gwlua/lua/compatinit.lua:0
```

Every one of the 59 games at
https://bot.libretro.com/assets/cores/HandheldElectronicGame/ passes an
image rather than a table, so every one of them takes the compat path:

```lua
-- Parachute (Nintendo, Wide Screen), main.bs
return system.init(
  unit1.form1.im_background,   -- an image, not a table -> compatinit
  keymap,
  ...
)
```

55 pass `unit1.form1.im_background`; the four Multi Screen units (Donkey
Kong, Donkey Kong II, Lifeboat, Mario Bros.) pass
`unit1.form1.im_background_open`. None passes a table. The modern pointer
path is therefore code that no published content reaches.

## Measurements

Taken with a headless harness that dlopens the core, scripts input, and
hashes every frame, so two runs compare mechanically.

Tap positions swept across the whole panel, held 30-40 frames, against a
baseline run with no input:

| Game | tap positions | changed output | joypad Select |
|---|---|---|---|
| Parachute (Nintendo, Wide Screen) | 35 | 0 | changed |
| Donkey Kong (Coleco) | 15 | 0 | changed |
| Egg (Nintendo, Wide Screen) | 15 | 0 | changed |
| Mickey Mouse (Nintendo, Wide Screen) | 15 | 0 | changed |

Aiming precisely fails the same way. Parachute draws GAME A at
(554, 43, 40, 29) on a 658x395 panel; a press held 30 frames on its centre
leaves the frame hash byte-identical to a run with no input at all, while
any joypad press changes it immediately.

The core is listening throughout. Over 600 frames it made 1800 pointer
queries, three per frame, all on port 2, and the harness answered 90 of
them with a press at the button's centre. Polled, delivered, discarded.

## Desktop RetroArch reproduction

RetroArch 1.22.2 on macOS, windowed, Parachute (Nintendo, Wide Screen):

1. **Baseline**: the game sits on its static all-segments splash.
2. **Mouse click on the drawn GAME A button** (a real HID-level click on
   its exact rectangle, which the game declares at 554,43,40,29 on its
   658x395 panel): the screenshot is **byte-identical** to the baseline.
3. **Keyboard Select**: the hand cursor appears over GAME A with its
   label. The frame changes.
4. **Keyboard Start**: Game A starts and plays normally.

So the same session, same build, same game: keyboard input works end to
end, and a click on the button the artwork invites you to press does
nothing at all.

## Why the fix is small

The games already declare everything a hit test needs. `unit1.bs` carries
each control's exact rectangle, and the simulators were desktop
applications, so they still carry their original mouse handlers:

```lua
self.btn_game_a_top.left = 554
self.btn_game_a_top.top = 43
self.btn_game_a_top.width = 40
self.btn_game_a_top.height = 29
self.btn_game_a_top.hint = "click to start game A"
self.btn_game_a_top.onmousedown = ( self.btn_game_a_topmousedown )
```

`compatinit` already registers these controls and already builds the
keymap and hand menu around them. What it lacks is the pointer read and
the hit test that `system.lua` performs on the same data.

Two directions, and the choice is an architectural one for you rather
than for me:

1. Port `system.lua`'s pointer block into `compatinit.lua`, hit-testing
   the controls it already registers.
2. Route the compat games through the modern path by adapting their
   arguments at the dispatch, so there is one pointer implementation
   rather than two.

Happy to prepare a PR for whichever you prefer.

## Environment

- Core 1.6.3, commit `dddc9d553f7503f17c3dfd1906bc94c07eff8515` (the
  core logs its own `GW_GITHASH` at load). Harness measurements on an
  arm64 build of that commit; the RetroArch repro on an x86_64 build of
  the same commit under RetroArch 1.22.2.
- Game files as published at bot.libretro.com
- The behaviour is entirely in the Lua, so it is not platform specific
