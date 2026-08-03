"""Prototype decoder for Chroma .rde design files.

Format facts established over a 127-file corpus and validated against companion
DST exports and Chroma's own embedded preview images:

  - File is a flat sequence of TLV sections starting at 0xA0:
        u8 tag, u32 payload_length, payload[payload_length]
    Tags 0x01..0x11 are plaintext metadata (0x01 is a BMP preview, 0x02 the
    thread list); tags 0x64..0x6A carry design objects. The walk consumes the
    file exactly on all 127 corpus files.
  - Object payloads are XOR-obfuscated with a glibc-style LCG keystream
    seeded with the payload length itself:
        s = (s * 1103515245 + 12345) mod 2**32,  byte = (s >> 16) & 0xFF
    Same-length payloads therefore share a keystream -- that reuse is what
    exposed the scheme (equal-length sections XOR to mostly zeros, even
    across different files).
  - Decrypted object payload: u16 thread index, u32 stitch count, the stitch
    list, then the editable outline, then a parameter block. See read_objects
    and read_outlines for the two record layouts, both of which are variable
    length -- assuming a fixed stride silently desynchronises the stream.
  - Coordinates are float32 in 0.1 mm, in the same space as the exported DST.
    Verified: decoded bbox 1470.2 x 565.0 == DST bbox 147.0 mm x 56.5 mm.

Not yet mapped: the named stitch parameters (the block begins at a 6E 02 00 00
marker), and the ~27% of objects whose outline block uses a different preamble.
Object kind (fill / satin / run) is NOT encoded in the section tag -- it is
recovered by measuring the stitch geometry instead (see satin_runs).
"""
import math
import struct

MASK32 = (1 << 32) - 1

# Object-carrying section tags. All share the same cipher and record layout;
# the tag distinguishes object kind (fill / column / run / ...), which is not
# yet mapped. Seen across the corpus: 0x64 and 0x67 are the most common.
OBJECT_TAGS = (0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A)


def keystream(length):
    s = length
    out = bytearray()
    for _ in range(length):
        s = (s * 1103515245 + 12345) & MASK32
        out.append((s >> 16) & 0xFF)
    return bytes(out)


def decrypt(payload):
    return bytes(a ^ b for a, b in zip(payload, keystream(len(payload))))


def read_sections(path):
    data = open(path, 'rb').read()
    off, out = 0xA0, []
    while off < len(data):
        tag = data[off]
        length = struct.unpack_from('<I', data, off + 1)[0]
        if off + 5 + length > len(data):
            break
        out.append((tag, data[off + 5:off + 5 + length]))
        off += 5 + length
    return out


def read_wstr(buf, off):
    n = struct.unpack_from('<I', buf, off)[0]
    return buf[off + 4:off + 4 + 2 * n].decode('utf-16-le'), off + 4 + 2 * n


def read_colors(payload):
    """Thread list from the plaintext tag-0x02 section."""
    off = 0
    n = struct.unpack_from('<I', payload, off)[0]
    off += 4
    colors = []
    for _ in range(n):
        rgb = payload[off:off + 3]
        off += 4
        code, off = read_wstr(payload, off)
        name, off = read_wstr(payload, off)
        brand, off = read_wstr(payload, off)
        off += 4
        colors.append({'rgb': '#%02x%02x%02x' % tuple(rgb),
                       'code': code, 'name': name, 'brand': brand})
    return colors


def read_objects(path):
    """Yield (tag, color_index, [(x, y, flag), ...]) for each design object.

    The leading u16 is the object's index into the tag-0x02 thread list.
    Established over a 127-file corpus: it satisfied `value < len(threads)`
    for 19198 of 19198 object sections, with zero violations.
    """
    for tag, payload in read_sections(path):
        if tag not in OBJECT_TAGS:
            continue
        p = decrypt(payload)
        color_index, count = struct.unpack_from('<HI', p, 0)
        # Records are variable length: a flag of 0x04 (subpath start) carries
        # two extra bytes. Parsing at a fixed 9-byte stride desynchronises the
        # whole stream after the first such point.
        off, pts, overrun = 6, [], False
        for _ in range(count):
            if off + 9 > len(p):
                overrun = True
                break
            x, y, flag = struct.unpack_from('<ffB', p, off)
            off += 11 if flag == 0x04 else 9
            pts.append((x, y, flag))
        if overrun:
            continue
        if pts and not all(abs(v) < 1e5 for pt in pts for v in pt[:2]):
            continue  # record variant we have not mapped yet
        yield tag, color_index, pts


def satin_runs(points, min_stitches=6):
    """Find satin zigzag runs in a stitch list and split each into two rails.

    Chroma builds a satin from a completed closed shape; Ink/Stitch needs two
    rails. Rather than splitting the shape geometrically, this reads the rails
    straight off the stitches: in a satin the needle alternates between the two
    rails, so consecutive stitches reverse direction and alternate endpoints
    land on opposite rails. This also works for Chroma lettering objects, which
    store a font reference and no outline at all.

    Yields (start_index, end_index, rail_a, rail_b).
    """
    reversing = []
    for i in range(len(points) - 2):
        ax = points[i + 1][0] - points[i][0]
        ay = points[i + 1][1] - points[i][1]
        bx = points[i + 2][0] - points[i + 1][0]
        by = points[i + 2][1] - points[i + 1][1]
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if not (0.3 < la < 150 and 0.3 < lb < 150):
            reversing.append(False)
            continue
        turn = abs((math.atan2(by, bx) - math.atan2(ay, ax) + math.pi)
                   % (2 * math.pi) - math.pi)
        reversing.append(math.degrees(turn) > 140)

    i = 0
    while i < len(reversing):
        if not reversing[i]:
            i += 1
            continue
        j = i
        while j < len(reversing) and reversing[j]:
            j += 1
        if j - i >= min_stitches:
            seg = points[i:j + 2]
            yield i, j + 2, seg[0::2], seg[1::2]
        i = j


def read_outlines(path):
    """Yield (tag, color_index, [contour, ...]) — the editable Bezier outlines.

    This is the digitizer's actual vector artwork, not the generated stitches,
    which is what makes a design resizable: Ink/Stitch can regenerate stitches
    at any scale from these, whereas scaling baked stitches wrecks density.

    Layout, after the main stitch list ends:
        8 bytes, u32 == 2, pad, u32 == 1, pad, <section tag>,
        u32 contour_count, then per contour: u32 node_count, node records.
    A node record is 26 bytes -- two flag bytes then anchor, handle-in and
    handle-out as float32 pairs -- except when flag byte 1 has 0x80 set, which
    marks a handle-less corner and shortens the record to 10 bytes.
    """
    for tag, payload in read_sections(path):
        if tag not in OBJECT_TAGS:
            continue
        p = decrypt(payload)
        color_index, count = struct.unpack_from('<HI', p, 0)
        off = 6
        for _ in range(count):
            if off + 9 > len(p):
                break
            flag = p[off + 8]
            off += 11 if flag == 0x04 else 9
        q = off + 8
        if q + 19 > len(p):
            continue
        if struct.unpack_from('<I', p, q)[0] != 2:
            continue
        if struct.unpack_from('<I', p, q + 5)[0] != 1:
            continue
        if p[q + 10] != tag:
            continue
        ncontours = struct.unpack_from('<I', p, q + 11)[0]
        q += 15
        contours = []
        for _ in range(max(ncontours, 1)):
            if q + 4 > len(p):
                break
            nnodes = struct.unpack_from('<I', p, q)[0]
            q += 4
            if nnodes == 0:
                break
            nodes, ok = [], True
            for _ in range(nnodes):
                if q + 10 > len(p):
                    ok = False
                    break
                f0, f1 = p[q], p[q + 1]
                npt = 1 if f1 & 0x80 else 3
                if q + 2 + 8 * npt > len(p):
                    ok = False
                    break
                pts = [struct.unpack_from('<ff', p, q + 2 + 8 * k)
                       for k in range(npt)]
                if not all(abs(v) < 1e5 for pt in pts for v in pt):
                    ok = False
                    break
                nodes.append((f0, f1, pts))
                q += 2 + 8 * npt
            if not ok:
                break
            contours.append(nodes)
        if contours:
            yield tag, color_index, contours


def outlines_to_svg(path):
    """Render recovered outlines as filled Bezier paths, grouped by thread."""
    objs = list(read_outlines(path))
    colors = None
    for tag, payload in read_sections(path):
        if tag == 0x02:
            colors = read_colors(payload)
    pts = [pt for _, _, cs in objs for c in cs for n in c for pt in n[2]]
    minx = min(p[0] for p in pts)
    miny = min(p[1] for p in pts)
    w = max(p[0] for p in pts) - minx
    h = max(p[1] for p in pts) - miny
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
           f'width="{w/10:.2f}mm" height="{h/10:.2f}mm" '
           f'viewBox="0 0 {w:.2f} {h:.2f}">',
           '<rect width="100%" height="100%" fill="white"/>']
    for tag, ci, contours in objs:
        color = colors[ci]['rgb'] if colors else '#000'
        for nodes in contours:
            anchor = lambda n: n[2][0]
            hout = lambda n: n[2][2] if len(n[2]) == 3 else n[2][0]
            hin = lambda n: n[2][1] if len(n[2]) == 3 else n[2][0]
            a0 = anchor(nodes[0])
            d = [f'M {a0[0]-minx:.2f},{a0[1]-miny:.2f}']
            for i in range(len(nodes)):
                a, b = nodes[i], nodes[(i + 1) % len(nodes)]
                c1, c2, e = hout(a), hin(b), anchor(b)
                d.append(f'C {c1[0]-minx:.2f},{c1[1]-miny:.2f} '
                         f'{c2[0]-minx:.2f},{c2[1]-miny:.2f} '
                         f'{e[0]-minx:.2f},{e[1]-miny:.2f}')
            out.append(f'<path d="{" ".join(d)} Z" fill="{color}" '
                       f'fill-opacity="0.6" stroke="{color}" '
                       f'stroke-width="0.8"/>')
    out.append('</svg>')
    return '\n'.join(out)


def to_svg(path):
    objs = list(read_objects(path))
    colors = None
    for tag, payload in read_sections(path):
        if tag == 0x02:
            colors = read_colors(payload)
    xs = [p[0] for _, _, pts in objs for p in pts]
    ys = [p[1] for _, _, pts in objs for p in pts]
    minx, miny = min(xs), min(ys)
    w, h = max(xs) - minx, max(ys) - miny
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
           f'width="{w/10:.2f}mm" '
           f'height="{h/10:.2f}mm" viewBox="0 0 {w:.2f} {h:.2f}">',
           '<rect width="100%" height="100%" fill="white"/>']
    # One group per thread, in stitch order, so the structure survives import.
    by_color = {}
    for tag, color_index, pts in objs:
        by_color.setdefault(color_index, []).append((tag, pts))
    for color_index in sorted(by_color):
        thread = colors[color_index] if colors else None
        stroke = thread['rgb'] if thread else '#000'
        label = (f"{thread['code']} {thread['name']}" if thread
                 else f'color {color_index}')
        out.append(f'<g id="color{color_index}" '
                   f'inkscape:groupmode="layer" inkscape:label="{label}">')
        for tag, pts in by_color[color_index]:
            d = 'M ' + ' L '.join(f'{x-minx:.2f},{y-miny:.2f}'
                                  for x, y, _ in pts)
            out.append(f'  <path d="{d}" fill="none" stroke="{stroke}" '
                       f'stroke-width="1.5" opacity="0.85"/>')
        out.append('</g>')
    out.append('</svg>')
    return '\n'.join(out)


if __name__ == '__main__':
    import sys
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else 'out.svg'
    open(dst, 'w').write(to_svg(src))
    objs = list(read_objects(src))
    print(f'{src}: {len(objs)} objects, '
          f'{sum(len(p) for _, _, p in objs)} points -> {dst}')
