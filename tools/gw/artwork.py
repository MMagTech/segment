#!/usr/bin/env python3
"""Renders a MAME external artwork pack down to one handheld panel.

A pack is a zip holding a default.lay and the scans it references. The
.lay is a MAME layout: named elements (each wrapping an image, rect or
disk), and views that place those elements and the emulated screen in a
shared coordinate space. Packs in the wild differ wildly in how they do
that, so nothing here keys off a pack's shape:

  1. Pick the view that shows the bare unit, by name then by geometry.
  2. Flatten it to a draw list (groups and collections expanded, button
     press-animations dropped) in document order.
  3. Call the smallest image that still contains the screen the unit,
     and make its bounds the panel frame. That is what crops the room
     backdrop away without needing to know a backdrop is present.
  4. Composite every item into that frame at the unit's own resolution,
     honouring alpha and the multiply blend overlays use.
  5. Report the screen rectangle in panel pixels.

The screen itself is not drawn: the caller fills it with segments. It is
left as the artwork renders it (a printed LCD background is real detail
worth keeping) unless it comes out empty or dark, which means the pack
expected the emulator to supply every lit pixel; then it gets the usual
LCD grey-green so dark segments read against it.

Run standalone to inspect or preview a pack:
  python3 tools/gw/artwork.py <pack.zip> [preview.png]
"""
import io, os, sys, zipfile
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image

# The idle colour of an LCD with no printed background behind it.
LCD_GREY = (150, 161, 143)

# Native resolution is kept unless the panel would exceed this; packing
# all 175 games wants a smaller cap, one game at a time does not.
MAX_PANEL = (2560, 2560)

# How far a pixel must move under a press to count as part of the button.
PRESS_THRESHOLD = 60


class ArtworkError(Exception):
    pass


# ---------------------------------------------------------------- layout

def _bounds(node):
    """<bounds> comes in x/y/width/height or left/top/right/bottom form."""
    b = node.find('bounds')
    if b is None:
        return None
    a = b.attrib
    if 'width' in a or 'height' in a or 'x' in a or 'y' in a:
        return (float(a.get('x', 0)), float(a.get('y', 0)),
                float(a.get('width', 0)), float(a.get('height', 0)))
    l, t = float(a.get('left', 0)), float(a.get('top', 0))
    return (l, t, float(a.get('right', 0)) - l, float(a.get('bottom', 0)) - t)


def _alpha(node):
    c = node.find('color')
    return float(c.get('alpha', 1)) if c is not None else 1.0


def _union(rects):
    xs0 = min(r[0] for r in rects); ys0 = min(r[1] for r in rects)
    xs1 = max(r[0] + r[2] for r in rects); ys1 = max(r[1] + r[3] for r in rects)
    return (xs0, ys0, xs1 - xs0, ys1 - ys0)


def _contains(outer, inner, slack=0.03):
    """Artists let a screen overhang its background by a few pixels, so
    containment is judged with a tolerance scaled to the screen."""
    sx = max(1.0, inner[2] * slack)
    sy = max(1.0, inner[3] * slack)
    return (outer[0] <= inner[0] + sx and outer[1] <= inner[1] + sy and
            outer[0] + outer[2] >= inner[0] + inner[2] - sx and
            outer[1] + outer[3] >= inner[1] + inner[3] - sy)


class Item:
    """One thing to draw, with its bounds already in view coordinates."""
    __slots__ = ('kind', 'ref', 'rect', 'alpha', 'blend', 'index', 'tag', 'mask')

    def __init__(self, kind, ref, rect, alpha, blend, index, tag=None, mask=None):
        self.kind, self.ref, self.rect = kind, ref, rect
        self.alpha, self.blend, self.index = alpha, blend, index
        self.tag, self.mask = tag, mask       # the input this one reports

    def __repr__(self):
        return '<%s %s %s>' % (self.kind, self.ref, tuple(round(v) for v in self.rect))


# Legacy MAME layer tags draw like plain elements; overlay multiplies.
_DRAWABLE = {'element', 'overlay', 'bezel', 'backdrop', 'cpanel', 'marquee'}


class Layout:
    def __init__(self, text):
        self.root = ET.fromstring(text)
        self.elements = {e.get('name'): e for e in self.root.findall('element')}
        self.groups = {g.get('name'): g for g in self.root.findall('group')}
        self.views = self.root.findall('view')

    def flatten(self, view):
        """Draw list in document order, group and collection refs expanded."""
        out = []
        self._walk(view, out, (1.0, 0.0, 1.0, 0.0), set())
        return out

    def _walk(self, node, out, xf, seen):
        sx, dx, sy, dy = xf
        for child in node:
            tag = child.tag.lower()
            if tag in ('bounds', 'color', 'param', 'script'):
                continue
            if tag == 'collection':
                self._walk(child, out, xf, seen)
                continue
            if tag == 'group':
                name = child.get('ref')
                grp = self.groups.get(name)
                if grp is None or name in seen:
                    continue
                inner = []
                self._walk(grp, inner, (1.0, 0.0, 1.0, 0.0), seen | {name})
                if not inner:
                    continue
                src = _bounds(grp) or _union([i.rect for i in inner])
                dst = _bounds(child) or src
                gx = dst[2] / src[2] if src[2] else 1.0
                gy = dst[3] / src[3] if src[3] else 1.0
                for i in inner:
                    x = dst[0] + (i.rect[0] - src[0]) * gx
                    y = dst[1] + (i.rect[1] - src[1]) * gy
                    i.rect = (x * sx + dx, y * sy + dy,
                              i.rect[2] * gx * sx, i.rect[3] * gy * sy)
                    out.append(i)
                continue
            rect = _bounds(child)
            if rect is None:
                continue
            rect = (rect[0] * sx + dx, rect[1] * sy + dy, rect[2] * sx, rect[3] * sy)
            if tag == 'screen':
                out.append(Item('screen', None, rect, _alpha(child),
                                child.get('blend'), int(child.get('index', 0))))
            elif tag in _DRAWABLE:
                ref = child.get('ref') or child.get('element')
                if ref is None:
                    continue
                blend = 'multiply' if tag == 'overlay' else None
                mask = child.get('inputmask')
                out.append(Item('element', ref, rect, _alpha(child), blend, 0,
                                child.get('inputtag'),
                                int(mask, 0) if mask is not None else None))

    # -------------------------------------------------------- view choice

    def _name_score(self, name):
        n = (name or '').lower()
        if 'unit only' in n or 'unit_only' in n:
            s = 100
        elif 'unit and backdrop' in n or 'unit with backdrop' in n or \
             'unit and background' in n or 'full unit' in n:
            s = 80
        elif 'original scan' in n:
            s = 70
        elif 'handheld' in n or 'artwork' in n:
            s = 60
        elif 'background' in n or 'backdrop' in n:
            s = 10          # the LCD art alone, no unit: last resort
        else:
            s = 40
        if 'zoom' in n:      s -= 30   # crops the unit
        if 'fan art' in n:   s -= 15
        if 'white' in n:     s -= 10
        if 'no shadow' in n: s += 1    # deterministic tie-break, cleaner cut
        return s

    def candidates(self, drawable):
        """(score, view, items, screen, unit) for every usable view."""
        out = []
        for v in self.views:
            items = self.flatten(v)
            screen = pick_screen(items)
            if screen is None:
                continue
            unit = pick_unit(items, screen, _bounds(v), drawable)
            if unit is None:
                continue
            out.append((self._name_score(v.get('name')), v, items, screen, unit))
        return out


def pick_screen(items, index=0):
    """The real screen window: the multiply-blended one when the pack
    draws a soft shadow copy as well, else the plain one."""
    screens = [i for i in items if i.kind == 'screen' and i.index == index]
    if not screens:
        return None
    for want in ('multiply', None, 'alpha', 'add'):
        for s in screens:
            if s.blend == want and s.alpha > 0.5:
                return s
    return screens[0]


def pick_unit(items, screen, view_bounds, drawable):
    """The unit is the largest image that still contains the screen.

    Largest, because the printed LCD background sits inside the unit and
    contains the screen too; the unit's own drop shadow ties with it on
    bounds, so later in document order wins, the shadow being drawn
    underneath. Elements that draw nothing in their default state are
    button press-animations laid over the whole unit, and tie without
    being the unit.

    The one thing bigger than the unit is a room backdrop, which is
    always laid down first and spans the whole view; it is dropped, but
    only when something else is left, since a pack whose unit fills its
    view looks identical by that test."""
    art = [i for i in items if i.kind == 'element' and drawable(i.ref)]
    if not art:
        return None
    frame = view_bounds or _union([i.rect for i in items])
    holding = [i for i in art if _contains(i.rect, screen.rect)]
    if len(holding) > 1 and holding[0] is art[0] and \
            holding[0].rect[2] * holding[0].rect[3] >= frame[2] * frame[3] * 0.9:
        holding = holding[1:]
    if not holding:
        return None
    area = lambda i: i.rect[2] * i.rect[3]
    best = max(area(i) for i in holding)
    return [i for i in holding if area(i) >= best * 0.98][-1]


# ---------------------------------------------------------------- images

class Pack:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        self.path = path
        self._cache = {}
        lays = [n for n in self.zip.namelist() if n.lower().endswith('.lay')]
        if not lays:
            raise ArtworkError('%s: no .lay' % os.path.basename(path))
        self.lay_name = sorted(lays)[0]
        self.layout = Layout(self.zip.read(self.lay_name).decode('utf-8', 'replace'))

    def _find(self, filename):
        """MAME matches artwork filenames case-insensitively."""
        want = filename.replace('\\', '/').lower()
        for n in self.zip.namelist():
            if n.lower() == want or n.lower().endswith('/' + want):
                return n
        return None

    def image(self, filename):
        if filename not in self._cache:
            n = self._find(filename)
            if n is None:
                self._cache[filename] = None
            else:
                im = Image.open(io.BytesIO(self.zip.read(n)))
                self._cache[filename] = im.convert('RGBA')
        return self._cache[filename]

    def component(self, ref, state=None):
        """The drawable component of a named element in a given state.

        Elements carry one component per state. state=None means the
        element's own default; pass '0' for an element wired to an input,
        because MAME drives those from the input rather than from
        defstate, so on an idle panel they are all unpressed. Reading
        defstate there put every button of a defstate="1" pack on the
        panel already pressed, and left no press image to locate it by."""
        el = self.layout.elements.get(ref)
        if el is None:
            return None
        want = el.get('defstate', '0') if state is None else state
        for kind in ('image', 'rect', 'disk'):
            for c in el.findall(kind):
                if c.get('state') in (None, want):
                    return kind, c
        return None

    def pressed(self, ref):
        """The component an element shows while its input is held."""
        el = self.layout.elements.get(ref)
        if el is None:
            return None
        states = sorted({c.get('state') for c in el.findall('image')
                         if c.get('state') not in (None, '0')})
        if not states:
            return None
        return self.component(ref, states[-1])

    def element_image(self, ref):
        c = self.component(ref)
        if c is None or c[0] != 'image':
            return None
        return self.image(c[1].get('file', ''))

    def draws(self, ref):
        """True when the element puts something on an idle panel."""
        c = self.component(ref)
        if c is None:
            return False
        return c[0] != 'image' or self.image(c[1].get('file', '')) is not None

    def candidates(self):
        return self.layout.candidates(self.draws)


# ------------------------------------------------------------ compositing

def _over(dst, x, y, src):
    """Straight-alpha source-over of a float RGBA patch, clipped."""
    h, w = src.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dst.shape[1], x + w), min(dst.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    s = src[y0 - y:y1 - y, x0 - x:x1 - x]
    d = dst[y0:y1, x0:x1]
    sa = s[..., 3:4]
    da = d[..., 3:4]
    oa = sa + da * (1 - sa)
    safe = np.where(oa > 0, oa, 1)
    d[..., :3] = (s[..., :3] * sa + d[..., :3] * da * (1 - sa)) / safe
    d[..., 3:4] = oa


def _multiply(dst, x, y, src):
    """MAME's multiply blend. Where nothing lies beneath, there is
    nothing to darken, so the source simply lands."""
    h, w = src.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dst.shape[1], x + w), min(dst.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    s = src[y0 - y:y1 - y, x0 - x:x1 - x]
    d = dst[y0:y1, x0:x1]
    sa = s[..., 3:4]
    empty = d[..., 3:4] <= 0.004
    factor = 1 - sa * (1 - s[..., :3])          # transparent source is a no-op
    d[..., :3] = np.where(empty, s[..., :3], d[..., :3] * factor)
    d[..., 3:4] = np.where(empty, sa, d[..., 3:4])


def _solid(rgba, w, h):
    a = np.empty((h, w, 4), np.float32)
    a[...] = np.array(rgba, np.float32) / 255.0
    return a


# ---------------------------------------------------------------- render

class Panel:
    """A rendered unit: RGBA bytes plus where its screen sits."""

    def __init__(self, w, h, rgba, lcd, view, scale, frame, screens, filled, lcds):
        self.w, self.h, self.rgba = w, h, rgba
        self.lcd = lcd
        self.lcds = lcds        # index -> rect, for the dual-screen units
        self.view = view
        self.scale = scale
        self.frame = frame
        self.screens = screens
        self.lcd_filled = filled

    def to_panel(self, x, y):
        """Map a point from .lay view coordinates into panel pixels, so
        button rectangles can be written in the layout's own units."""
        return (round((x - self.frame[0]) * self.scale),
                round((y - self.frame[1]) * self.scale))

    def to_panel_rect(self, r):
        x, y = self.to_panel(r[0], r[1])
        return (x, y, round(r[2] * self.scale), round(r[3] * self.scale))


def render(path, max_panel=MAX_PANEL, view_name=None, screen_index=0):
    pack = Pack(path)
    cands = pack.candidates()
    if not cands:
        raise ArtworkError('%s: no view with both a screen and a unit'
                           % os.path.basename(path))
    if view_name:
        cands = [c for c in cands if c[1].get('name') == view_name] or cands
    # Best name, then the view whose unit fills most of its own frame.
    score, view, items, screen, unit = max(
        cands, key=lambda c: (c[0], c[4].rect[2] * c[4].rect[3]))
    if screen_index:
        alt = pick_screen(items, screen_index)
        if alt is not None:
            screen = alt

    frame = unit.rect
    src = pack.element_image(unit.ref)
    scale = (src.width / frame[2]) if src and frame[2] else 1.0
    scale = min(scale, max_panel[0] / frame[2], max_panel[1] / frame[3])
    pw, ph = max(1, round(frame[2] * scale)), max(1, round(frame[3] * scale))

    canvas = np.zeros((ph, pw, 4), np.float32)
    place = lambda r: (round((r[0] - frame[0]) * scale), round((r[1] - frame[1]) * scale),
                       max(1, round(r[2] * scale)), max(1, round(r[3] * scale)))

    lcd = place(screen.rect)
    lcds = {}
    for idx in sorted({i.index for i in items if i.kind == 'screen'}):
        s2 = pick_screen(items, idx)
        if s2 is not None:
            lcds[idx] = place(s2.rect)
    for it in items:
        if it.kind == 'screen':
            continue
        x, y, w, h = place(it.rect)
        if x >= pw or y >= ph or x + w <= 0 or y + h <= 0:
            continue
        comp = pack.component(it.ref, '0' if it.tag else None)
        if comp is None:
            continue
        kind, node = comp
        if kind == 'image':
            im = pack.image(node.get('file', ''))
            if im is None:
                continue
            arr = np.asarray(im.resize((w, h), Image.LANCZOS), np.float32) / 255.0
            arr = arr.copy()
        else:
            c = node.find('color')
            rgb = (float(c.get('red', 0)) * 255, float(c.get('green', 0)) * 255,
                   float(c.get('blue', 0)) * 255) if c is not None else (0, 0, 0)
            arr = _solid((rgb[0], rgb[1], rgb[2], 255), w, h)
            if kind == 'disk':
                yy, xx = np.mgrid[0:h, 0:w]
                m = (((xx - w / 2 + .5) / (w / 2)) ** 2 +
                     ((yy - h / 2 + .5) / (h / 2)) ** 2) <= 1
                arr[..., 3] *= m
        if it.alpha < 1:
            arr[..., 3] *= it.alpha
        (_multiply if it.blend == 'multiply' else _over)(canvas, x, y, arr)

    filled = any([_finish_screen(canvas, r) for r in lcds.values()])
    pack.zip.close()      # a 175-game batch would otherwise run out of handles
    rgba = bytearray((np.clip(canvas, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
    return Panel(pw, ph, rgba, lcd, view.get('name'), scale, frame,
                 sorted(lcds), filled, lcds)


def buttons(path, panel=None):
    """Where each button sits, taken from the pack's own press images.

    A layout's control group only ever gives full-unit bounds, so it
    cannot say where a button is. The images it references can: each is
    a full-size overlay that draws one button in its pressed state, and
    it carries the inputtag and inputmask naming the input that button
    reports. Compositing one over the idle panel and taking the bounding
    box of what changed locates the button, and the change is wider than
    the button itself only because the artist drew its shadow moving
    too, which makes for a fair touch target.

    Returns [{'rect': (x, y, w, h) in panel pixels, 'tag', 'mask', 'ref'}].
    Empty when a pack carries no press images, which is most of them.
    """
    pack = Pack(path)
    if panel is None:
        panel = render(path)
    cands = pack.candidates()
    if not cands:
        return []
    _, view, items, _, _ = max(cands, key=lambda c: (c[0], c[4].rect[2] * c[4].rect[3]))

    base = np.frombuffer(bytes(panel.rgba), np.uint8).reshape(panel.h, panel.w, 4)
    base = base[..., :3].astype(np.int16)
    out = []
    for it in items:
        if it.kind != 'element' or it.tag is None or not it.mask:
            continue
        comp = pack.pressed(it.ref)
        if comp is None or comp[0] != 'image':
            continue
        im = pack.image(comp[1].get('file', ''))
        if im is None:
            continue
        x, y, w, h = panel.to_panel_rect(it.rect)
        if w < 2 or h < 2:
            continue
        arr = np.asarray(im.resize((w, h), Image.LANCZOS), np.int16)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(panel.w, x + w), min(panel.h, y + h)
        if x1 <= x0 or y1 <= y0:
            continue
        src = arr[y0 - y:y1 - y, x0 - x:x1 - x]
        dst = base[y0:y1, x0:x1]
        a = src[..., 3:4] / 255.0
        diff = np.abs((src[..., :3] * a + dst * (1 - a)) - dst).max(axis=2)
        ys, xs = np.nonzero(diff > PRESS_THRESHOLD)
        if not len(xs):
            continue
        out.append({'rect': (x0 + int(xs.min()), y0 + int(ys.min()),
                             int(xs.max() - xs.min()) + 1, int(ys.max() - ys.min()) + 1),
                    'tag': it.tag, 'mask': it.mask, 'ref': it.ref})
    pack.zip.close()
    return _separate(out, panel.lcd)


def _separate(btns, lcd):
    """Two zones must not claim the same tap. Overlaps are shrunk about
    their centres rather than dropped, since the artist's shadow is what
    made them wide in the first place."""
    def shrink(r, f):
        cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
        w, h = max(8, r[2] * f), max(8, r[3] * f)
        return (int(cx - w / 2), int(cy - h / 2), int(w), int(h))

    def hits(a, b):
        return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or
                    a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])

    for _ in range(6):
        clash = [i for i, x in enumerate(btns)
                 for j, y in enumerate(btns) if i != j and hits(x['rect'], y['rect'])]
        if not clash:
            break
        for i in set(clash):
            btns[i]['rect'] = shrink(btns[i]['rect'], 0.8)
    return btns


def _finish_screen(canvas, lcd):
    """Leave a printed LCD background alone; supply one when the pack
    left the window empty or black for the emulator to light up."""
    x, y, w, h = lcd
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(canvas.shape[1], x + w), min(canvas.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return False
    win = canvas[y0:y1, x0:x1]
    opaque = float(win[..., 3].mean())
    lum = float((win[..., :3] * win[..., 3:4]).mean())
    if opaque > 0.9 and lum > 0.22:
        return False
    win[...] = _solid((*LCD_GREY, 255), x1 - x0, y1 - y0)
    return True


# ------------------------------------------------------------------ main

def main():
    if len(sys.argv) < 2:
        print('usage: artwork.py <pack.zip> [preview.png]'); return 2
    p = render(sys.argv[1])
    print('%-14s view=%-34s panel=%dx%d scale=%.3f lcd=%s%s screens=%s' % (
        os.path.basename(sys.argv[1]), p.view, p.w, p.h, p.scale, p.lcd,
        ' (filled)' if p.lcd_filled else '', p.screens))
    if len(sys.argv) > 2:
        im = Image.frombytes('RGBA', (p.w, p.h), bytes(p.rgba))
        im.save(sys.argv[2])
        print('wrote', sys.argv[2])
    return 0


if __name__ == '__main__':
    sys.exit(main())
