# Getting started

A step-by-step walkthrough, from nothing to a handheld game playing in
RetroArch. No programming needed; you run one command per game.

If a step's command fails, the exact error and a fix are in
[Troubleshooting](#troubleshooting) at the bottom.

---

## What this does, in one sentence

You give it a MAME romset for an LCD handheld (a Game & Watch, a Tiger
game, etc.) plus the scanned artwork of that unit, and it produces one
`.mgw` file that plays on RetroArch's stock "Handheld Electronic Game"
core, with the real chip emulated inside the file.

## 1. Get the tools

```
git clone https://github.com/MMagTech/segment.git
cd segment
```

## 2. Install the prerequisites

You need Python 3 with two libraries, and `rsvg-convert`.

**macOS** (with [Homebrew](https://brew.sh)):
```
brew install python librsvg
pip3 install pillow numpy
```

**Linux (Debian/Ubuntu):**
```
sudo apt install python3 python3-pip librsvg2-bin
pip3 install pillow numpy
```

Check it worked:
```
python3 -c "import PIL, numpy; print('ok')" && rsvg-convert --version
```

## 3. Get a game's files

You supply these yourself (this repo ships no game data). Two pieces per
game:

- **The romset** — the MAME zip for the game, named the way MAME names
  it, e.g. `gnw_ball.zip` (Ball), `tsonic.zip` (Tiger Sonic). It holds
  the chip ROM and the screen's vector graphics. Use a MAME set that
  matches; 0.260 or newer covers almost everything.
- **The artwork pack** *(optional but recommended)* — the scanned photo
  of the physical unit, as a `.zip` with a `default.lay` inside. The
  community maintains these; without one you still get a clean generated
  console, and you can re-run later with the artwork to upgrade it.

To find the game's MAME short name, or to see what's already known,
open [COVERAGE.md](COVERAGE.md) — it lists every game, its short name,
and whether it needs a ROM, artwork, or nothing.

## 4. Build the game

One command. Point it at the romset, and the artwork pack if you have
one:

```
python3 tools/gw/convert.py gnw_ball.zip gnw_ball_artwork.zip
```

or, with no artwork:

```
python3 tools/gw/convert.py gnw_ball.zip
```

It narrates each step and writes a file named for the game, e.g.
`Ball (Nintendo).mgw`, in the current folder. If you have a gw-libretro
core on your machine it also boots the game headless and confirms it
responds, so it won't hand you a broken file.

Expected output looks like:
```
convert: game: Ball (Nintendo)  [chip sm5a, 1 screen(s)]
convert: romset: program ac-01, 1 screen SVG(s)
convert: artwork: view 'Handheld_Game_Artwork', panel 2227x1499, screen(s) 1069x692
convert: 5 tap zones from the pack's press images
convert: wrote Ball (Nintendo).mgw (1.8 MB)
convert: self-test passed: boots and responds
```

## 5. Play it in RetroArch

1. In RetroArch, install the core **"Handheld Electronic Game (GW)"**
   (Online Updater → Core Downloader). This is the stock core; nothing
   here modifies it.
2. Copy your `.mgw` file into your RetroArch content folder.
3. Load Content → pick the `.mgw` → run with the Handheld Electronic
   Game core.

It plays like any of the 59 games that core already ships with, except
this one is the real chip program wearing the scanned unit, and the
drawn buttons are tappable on a touchscreen.

## Building a lot of games

`convert.py` does one game at a time. To do many, loop over your
romsets in the shell; e.g. on macOS/Linux:

```
for rom in romsets/*.zip; do
  name=$(basename "$rom" .zip)
  python3 tools/gw/convert.py "$rom" "artwork/$name.zip"
done
```

(Adjust the paths to wherever you keep your romsets and artwork.)

---

## Troubleshooting

**`'<name>' is not a romset this tool knows`** — the zip's name must
match MAME's short name (e.g. `gnw_ball.zip`, not `Ball.zip`). Rename it.

**`runs on the sm530, which has no verified core yet`** — that game's
chip is not emulated yet; see COVERAGE.md. Nothing you can do but wait.

**`the romset carries no screen SVG`** — your romset predates MAME's
vector screen for that game. Use a newer MAME set.

**`No module named 'PIL'` / `'numpy'`** — the Python libraries aren't
installed: `pip3 install pillow numpy`.

**`rsvg-convert: command not found`** — install librsvg (step 2).

**The game builds but the self-test says "changed nothing"** — some
units wake on a button other than Game A (Select, or a keypad). It's
usually still fine; try it in RetroArch.

**The panel looks small / low-resolution** — a few games with very dense
LCD art are shrunk to fit a fixed buffer in the stock core. They still
play; a fuller-resolution version needs a core-side fix (see FINDINGS.md).
