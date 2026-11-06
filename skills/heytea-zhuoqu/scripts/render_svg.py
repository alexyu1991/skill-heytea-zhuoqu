#!/usr/bin/env python3
"""Render designed centerline strokes to self-contained SVG; stdlib only."""
import argparse
import json
import math
from pathlib import Path
import random
import re
import secrets
import xml.etree.ElementTree as ET

NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)
NUMBER = re.compile(r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?')
COMMAND = re.compile(r'([MLQCZ])([^MLQCZ]*)')
ARITY = {'M': 2, 'L': 2, 'Q': 4, 'C': 6, 'Z': 0}


def finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Coordinates and dimensions must be finite')
    return number


def fmt(value):
    return format(finite(value), '.6g')


def pair(value):
    if len(value) != 2:
        raise ValueError('Expected a coordinate pair')
    return tuple(finite(n) for n in value)


def parse_path(d):
    matches = list(COMMAND.finditer(d))
    if not matches or matches[0].start() != 0 or matches[0][1] != 'M':
        raise ValueError('Paths must begin with an absolute M command')
    result = []
    for match in matches:
        kind, payload = match[1], match[2]
        if NUMBER.sub('', payload).strip(' ,\t\r\n'):
            raise ValueError(f'Unsupported path syntax: {match[0]}')
        numbers = [finite(v) for v in NUMBER.findall(payload)]
        if len(numbers) != ARITY[kind]:
            raise ValueError(f'Use one explicit command per segment: {match[0]}')
        result.append((kind, numbers, match.start(), match.end()))
    return result


def horizontal_runs(d, minimum):
    """Find near-horizontal left-to-right runs beginning at each pen lift."""
    commands = parse_path(d)
    runs = []
    for i, (kind, start, _, _) in enumerate(commands):
        if kind != 'M':
            continue
        previous, last = start, i
        for j in range(i + 1, len(commands)):
            next_kind, values, _, _ = commands[j]
            if next_kind not in ('L', 'Q'):
                break
            end = values[-2:]
            dx, dy = end[0] - previous[0], end[1] - previous[1]
            if dx <= 0 or abs(dy) > dx * .16:
                break
            if next_kind == 'Q' and abs(values[1] - previous[1]) > dx * .16 + 2:
                break
            previous, last = end, j
        if previous[0] - start[0] >= minimum:
            runs.append((commands[i + 1][2], commands[last][3], start, previous))
    return runs


def fold_path(d, rng, minimum, cap=None, enabled=True):
    runs = horizontal_runs(d, minimum)  # Also validates paths excluded from folding.
    edits = []
    for begin, finish, (x0, y0), (x1, y1) in runs:
        # Always consume the same three samples, including for excluded runs.
        trigger = rng.random() < .6
        position = .24 + rng.random() * .1
        ratio = .065 + rng.random() * .025
        if not enabled or not trigger:
            continue
        length = x1 - x0
        depth = min(length * ratio, cap if cap is not None else math.inf)
        hx, hy = x0 + length * position, y0 + (y1 - y0) * position + depth
        first = [x0 + length * .1, y0, hx - length * .07, hy - depth * .33, hx, hy]
        second = [hx + length * .18, hy - depth * .38, x1 - length * .2, y1, x1, y1]
        replacement = 'C' + ' '.join(map(fmt, first)) + ' C' + ' '.join(map(fmt, second)) + ' '
        edits.append((begin, finish, replacement))
    for begin, finish, replacement in reversed(edits):
        d = d[:begin] + replacement + d[finish:]
    return d, len(edits)


def element(parent, tag, attrs=None, text=None):
    child = ET.SubElement(parent, f'{{{NS}}}{tag}', attrs or {})
    child.text = text
    return child


def render(spec, seed=None):
    text = spec['text']
    glyphs = spec['glyphs']
    if not isinstance(text, str) or not text.strip() or not glyphs:
        raise ValueError('A nonempty text and glyph list are required')
    drawn_text = ''.join(g['char'] for g in glyphs)
    if ''.join(drawn_text.split()) != ''.join(text.split()):
        raise ValueError('Glyphs must preserve the requested characters and reading order')
    view_box = spec.get('viewBox', [0, 0, 640, 480])
    if len(view_box) != 4:
        raise ValueError('viewBox needs four numbers')
    x, y, width, height = map(finite, view_box)
    stroke_width = finite(spec.get('stroke_width', 6.2))
    deformation = spec.get('deformation_strength', '高')
    freedom = spec.get('layout_freedom', '高')
    if deformation not in ('低', '中', '高') or freedom not in ('低', '中', '高'):
        raise ValueError('deformation_strength and layout_freedom must be 低, 中, or 高')
    minimum = finite(spec.get('long_horizontal_min', 80))
    if min(width, height, stroke_width, minimum) <= 0:
        raise ValueError('Canvas dimensions, line width, and threshold must be positive')
    if seed is None:
        seed = spec.get('seed')
    if seed is None:
        seed = secrets.randbits(32)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError('seed must be an integer')
    rng = random.Random(seed)
    svg = ET.Element(f'{{{NS}}}svg', {
        'viewBox': ' '.join(map(fmt, (x, y, width, height))),
        'role': 'img', 'aria-label': text, 'data-seed': str(seed),
        'data-deformation-strength': deformation, 'data-layout-freedom': freedom
    })
    element(svg, 'title', text=text)
    if spec.get('background'):
        element(svg, 'rect', {'x': fmt(x), 'y': fmt(y), 'width': fmt(width),
                             'height': fmt(height), 'fill': spec['background']})
    ink = element(svg, 'g', {'fill': 'none', 'stroke': spec.get('color', '#34332f'),
                           'stroke-width': fmt(stroke_width), 'stroke-linecap': 'round',
                           'stroke-linejoin': 'round'})
    folded, count = 0, 0
    for glyph in glyphs:
        gx, gy = pair(glyph.get('at', [0, 0]))
        group = element(ink, 'g', {'data-character': glyph['char'],
                                  'transform': f'translate({fmt(gx)} {fmt(gy)})'})
        for stroke in glyph['strokes']:
            if isinstance(stroke, str):
                stroke = {'d': stroke}
            setting = stroke.get('fold', {})
            if setting is False:
                allowed, cap = False, None
            elif isinstance(setting, dict):
                allowed, cap = True, setting.get('max_depth')
            else:
                raise ValueError('fold must be false or a settings object')
            if cap is not None:
                cap = finite(cap)
                if cap < 0:
                    raise ValueError('max_depth must be nonnegative')
            d, changes = fold_path(stroke['d'], rng, minimum, cap, allowed)
            attrs = {'d': d}
            if 'translate' in stroke:
                tx, ty = pair(stroke['translate'])
                attrs['transform'] = f'translate({fmt(tx)} {fmt(ty)})'
            element(group, 'path', attrs)
            folded += changes
            count += 1
    ET.indent(svg, space='  ')
    return ET.tostring(svg, encoding='unicode') + '\n', {'seed': seed, 'paths': count, 'folded': folded}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('drawing', type=Path, help='Designed strokes as JSON')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--seed', type=int, help='Override the saved seed for a new variant')
    args = parser.parse_args()
    try:
        svg, info = render(json.loads(args.drawing.read_text()), args.seed)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(svg)
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.exit(2, f'Cannot render SVG: {error}\n')
    print(json.dumps({'output': str(args.out.resolve()), **info}, ensure_ascii=False))


if __name__ == '__main__':
    main()
