#!/usr/bin/env python3
"""Extracts a MAME handheld romset's LCD segments as positioned bitmaps.

The SVG in an hh_sm510-family romset names every segment group with a
<title> matching the chip's output tag (o.y.h for the SM500 family).
For each segment this renders the SVG with every OTHER segment hidden,
crops to the segment's pixels, and emits a PNG plus a manifest of
positions, all scaled to the requested output width.

Run: python3 tools/gw/svg2segs.py <romset.svg> <outdir> [width]
Needs rsvg-convert (brew install librsvg).
"""
import json, os, re, subprocess, sys, zlib, struct

def read_png(path):
    d = open(path, 'rb').read()
    w, h = struct.unpack('>II', d[16:24])
    ctype = d[25]
    bpp = 4 if ctype == 6 else 3       # RGBA or RGB
    idat = b''
    i = 8
    while i < len(d):
        ln, typ = struct.unpack('>I4s', d[i:i+8])
        if typ == b'IDAT': idat += d[i+8:i+8+ln]
        i += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * bpp
    out = bytearray(w * h * 4)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        f = raw[pos]; pos += 1
        line = bytearray(raw[pos:pos+stride]); pos += stride
        if f == 1:
            for x in range(bpp, stride): line[x] = (line[x] + line[x-bpp]) & 255
        elif f == 2:
            for x in range(stride): line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x-bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x-bpp] if x >= bpp else 0
                b = prev[x]; c = prev[x-bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        out[y*stride:(y+1)*stride] = line
        prev = line
    if bpp == 3:   # expand to RGBA
        rgba = bytearray(w * h * 4)
        for i2 in range(w * h):
            rgba[i2*4:i2*4+3] = out[i2*3:i2*3+3]
            rgba[i2*4+3] = 255
        return w, h, rgba
    return w, h, out

def write_png(path, w, h, rgba):
    def chunk(t, data):
        c = t + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c))
    raw = b''.join(b'\x00' + bytes(rgba[y*w*4:(y+1)*w*4]) for y in range(h))
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))
    open(path, 'wb').write(png)

SEG_RE = re.compile(r'^\d+\.\d+\.\d+$')

def main():
    svg_path, outdir = sys.argv[1], sys.argv[2]
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 660
    os.makedirs(outdir, exist_ok=True)
    svg = open(svg_path).read()

    # Blocks carrying exactly one segment title. Most romsets wrap each
    # segment in its own <g>, but some mark the shape itself instead
    # (Chef does both, 11 groups and 61 bare paths), so any drawable tag
    # counts. "Exactly one" is what separates a segment from a container
    # that happens to hold segments.
    cands = []
    for tag in ('g', 'path', 'rect', 'circle', 'ellipse', 'polygon', 'polyline'):
        for m in re.finditer(r'<%s\b(?:(?!<%s\b).)*?</%s>' % (tag, tag, tag), svg, re.S):
            titles = [t for t in re.findall(r'<title[^>]*>([^<]+)</title>', m.group(0))
                      if SEG_RE.match(t.strip())]
            if len(titles) == 1:
                cands.append((m.span(), titles[0].strip(), tag))
    # outermost wins, so a segment wrapped in its own group is hidden as
    # the group rather than as the shape inside it
    cands.sort(key=lambda c: (c[0][0], -c[0][1]))
    groups = []
    for span, name, tag in cands:
        if groups and span[0] < groups[-1][0][1]:
            continue                      # nested inside one already taken
        groups.append((span, name, tag))
    names = [g[1] for g in groups]
    print('%d segments found' % len(names))

    def render(keep, path, hide_all_backgrounds):
        # hide every segment group not in `keep`
        out = []
        last = 0
        for (s, e), name, tag in groups:
            out.append(svg[last:s])
            if name in keep:
                out.append(svg[s:e])
            else:
                out.append(re.sub(r'^<%s\b' % tag, '<%s display="none"' % tag,
                                  svg[s:e], count=1))
            last = e
        out.append(svg[last:])
        body = ''.join(out)
        if hide_all_backgrounds:
            # hide the backdrop layer so segments render on transparency;
            # a style="display:inline" beats a display attribute, so edit
            # the style itself
            def kill(m):
                tag = m.group(0)
                if 'display:inline' in tag:
                    return tag.replace('display:inline', 'display:none')
                if 'style="' in tag:
                    return tag.replace('style="', 'style="display:none;', 1)
                return tag[:-1] + ' display="none">'
            body = re.sub(r'<g\b[^>]*inkscape:label="white"[^>]*>', kill,
                          body, count=1)
        r = subprocess.run(['rsvg-convert', '-w', str(width), '--format', 'png',
                            '-o', path], input=body.encode(), capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode()[:200])

    tmp = os.path.join(outdir, '_tmp.png')
    manifest = {}
    # background: everything hidden except the backdrop
    render(set(), os.path.join(outdir, 'backdrop.png'), False)
    bw, bh, _ = read_png(os.path.join(outdir, 'backdrop.png'))
    manifest['_canvas'] = [bw, bh]

    for i, name in enumerate(names):
        render({name}, tmp, True)
        w, h, px = read_png(tmp)
        x0, y0, x1, y1 = w, h, -1, -1
        for y in range(h):
            row = y * w * 4
            for x in range(w):
                if px[row + x*4 + 3] > 8:
                    if x < x0: x0 = x
                    if x > x1: x1 = x
                    if y < y0: y0 = y
                    if y > y1: y1 = y
        if x1 < 0:
            print('  %s: EMPTY' % name); continue
        cw, ch = x1-x0+1, y1-y0+1
        crop = bytearray(cw*ch*4)
        for y in range(ch):
            src = ((y0+y)*w + x0)*4
            crop[y*cw*4:(y+1)*cw*4] = px[src:src+cw*4]
        fn = 'seg_%s.png' % name.replace('.', '_')
        write_png(os.path.join(outdir, fn), cw, ch, crop)
        manifest[name] = {'x': x0, 'y': y0, 'w': cw, 'h': ch, 'file': fn}
        if (i+1) % 10 == 0: print('  %d/%d' % (i+1, len(names)))

    os.remove(tmp)
    json.dump(manifest, open(os.path.join(outdir, 'segments.json'), 'w'),
              indent=1, sort_keys=True)
    print('wrote %s (%d segments, canvas %dx%d)'
          % (os.path.join(outdir, 'segments.json'), len(manifest)-1, bw, bh))

if __name__ == '__main__': main()
