# Placing tap zones (hand-work guide)

For the 113 games in `tapzone_queue.json`: packs that draw a real console
with visible buttons but ship no pressed-state overlays, so the buttons
must be read off the render by eye and verified with the bench. Proven on
`tbatman` (7 zones, all tap==pad). This guide is the exact recipe.

## Data format

`tools/gw/tapzones.json`, keyed by MAME short name. Each zone is
`[fx, fy, fw, fh, action]` — a rectangle as FRACTIONS of the panel
(0..1), so it survives the pixel-budget rescale. `build_mgw.py` uses
this automatically when a pack has no press images (see
`load_manual_taps`). Example (tbatman):

```json
"tbatman": [
 [0.109,0.746,0.051,0.065,"up"], [0.195,0.806,0.055,0.060,"right"],
 [0.109,0.884,0.051,0.065,"down"], [0.766,0.690,0.141,0.069,"b1"],
 [0.766,0.792,0.141,0.069,"b2"], [0.477,0.646,0.051,0.044,"time"],
 [0.561,0.646,0.051,0.044,"start"]
]
```

## The per-game loop

1. **Get the actions and their printed names** from the driver wiring:
   ```
   python3 -c "import json;e=json.load(open('tools/gw/inputs.json'))['<rom>'];\
   print([(a,l.get(str(m),'')) for i,(c,l) in enumerate(zip(e['columns'],e['labels'])) for a,m in c.items()], e['pins'])"
   ```
   Actions are what you label zones with (`up/down/left/right`, `jump`,
   `b1`/`b2`, `time`, `start`, `gamea`/`gameb`, etc.). The printed name
   (e.g. "Pick", "Attack") tells you which drawn button it is.

2. **Render the unit with a fractional grid** and read button positions
   by eye:
   ```
   python3 - <<'PY'
   import sys; sys.path.insert(0,'tools/gw'); import artwork
   from PIL import Image,ImageDraw
   p=artwork.render('roms/artwork/<rom>.zip')
   im=Image.frombytes('RGBA',(p.w,p.h),bytes(p.rgba)).convert('RGB'); d=ImageDraw.Draw(im)
   for x in range(0,p.w,round(p.w/20)): d.line([(x,0),(x,p.h)],fill=(0,200,255)); d.text((x+2,2),'%.2f'%(x/p.w),fill=(0,150,255))
   for y in range(0,p.h,round(p.h/20)): d.line([(0,y),(p.w,y)],fill=(0,200,255)); d.text((2,y+2),'%.2f'%(y/p.h),fill=(0,150,255))
   im.thumbnail((900,1100)); im.save('/tmp/grid.png')
   PY
   ```
   Open /tmp/grid.png, read each button's fractional (x,y,w,h).

3. **Write the zones** into tapzones.json under the rom's key.

4. **Verify** each zone reproduces its action byte-for-byte. Build the
   game (`convert.py roms/mame/<rom>.zip roms/artwork/<rom>.zip`), then
   for each zone tap its centre and compare to the pad button:
   - retropad ids: up=4 down=5 left=6 right=7 A=8 B=0 X=9 Y=1 L1=10 R1=11 L2=12 select=2 start=3
   - action->retropad (from build_mgw RETROPAD): gamea<-start/l1, gameb<-r1,
     time<-l2, b1<-a, b2<-y, jump<-a, hit<-y/x, start<-start, pause<-select,
     up/down/left/right<-same
   - tap: `-tap 200,<px>,<py>,400,2` where px=round(cx/W*65535-32767),
     py=round(cy/H*65535-32767), cx/cy = zone centre in panel px
   - a correct zone: tap hash == pad hash, and != idle hash

## Speed: template by family

`tapzone_queue.json` groups the 113 by layout family. Within a family the
buttons sit at nearly the same fractions, so place one carefully, then
apply its zones to siblings and nudge:

- **nintendo_dual (15)**: d-pad bottom-left, JUMP bottom-right,
  GAME A/B/TIME pills stacked mid-right. gnw_dkong is a good template.
- **tiger (62)**: the standard Tiger unit — 3-or-4-way d-pad left, two
  action buttons right (labelled per game), OFF/SOUND/.../ON-START row
  across the middle. tbatman is the template (already done).
- **konami (23)**: similar to Tiger; d-pad left, action buttons right.
  ktmnt2 or ktopgun a good start.
- **nintendo_single / other (13)**: read individually, they vary.

Verify at least a few per family with the bench; artists shift buttons
enough that blind templating will miss some.
