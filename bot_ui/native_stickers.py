"""Constrained native WeChat sticker search/favorites; never type a chat message."""
import hashlib
import json
import re
import subprocess
import sys
import time
from PIL import Image
from PIL import ImageChops
from Xlib import display, error
try:
    from . import ui
except ImportError:
    import ui


def panel(d, w):
    scale = ui.desktop_scale(d)
    px = lambda value: round(value * scale)
    root = d.screen().root
    origin = root.translate_coords(w.id, 0, 0)
    matches = []
    for child in root.query_tree().children:
        # Clipboard helpers and popups can disappear after query_tree().
        try:
            g = child.get_geometry()
            mapped = child.get_attributes().map_state == 2
        except (error.BadWindow, error.BadDrawable):
            continue
        if (mapped and px(450) <= g.width <= px(490) and px(460) <= g.height <= px(500)
                and abs(g.x - origin.x - px(68)) <= px(4) and abs(g.y - origin.y - px(93)) <= px(4)):
            matches.append(g)
    if len(matches) != 1:
        raise RuntimeError('Sticker panel is not uniquely visible')
    return matches[0]


def picture(d, g):
    data = d.screen().root.get_image(g.x, g.y, g.width, g.height, 2, 0xffffffff)
    image = Image.frombytes('RGB', (g.width, g.height), data.data, 'raw', 'BGRX')
    scale = ui.desktop_scale(d)
    return image.resize((round(g.width / scale), round(g.height / scale))) if scale != 1 else image


def header(image, source):
    # Exclude the blinking search caret; query text is independently copied/checked.
    return hashlib.sha256(image.crop((0, 65 if source == 'search' else 0,
                                    image.width, 100 if source == 'search' else 35)).tobytes()).hexdigest()


def visible_cells(image, source):
    """Only detect occupied thumbnail slots, never infer what a sticker depicts."""
    cells = []
    y = 115 if source == 'search' else 20
    for column in range(1, 6):
        x = 20 + 88*(column-1)
        tile = image.crop((x, y, x+72, y+72)).convert('RGB')
        # Blank/loading tiles and simple add buttons are not selectable media.
        colors = tile.getcolors(72*72) or []
        difference = ImageChops.difference(tile, Image.new('RGB', tile.size, (255,255,255))).convert('L')
        mask = difference.point(lambda p: 255 if p>24 else 0)
        box = mask.getbbox()
        occupied = mask.histogram()[255]
        outside_plus = sum(mask.getpixel((xx, yy))>0 for yy in range(5,67) for xx in range(5,67)
                           if not (29<=xx<=43 or 29<=yy<=43))
        if len(colors)>12 and box and box[2]-box[0]>=32 and box[3]-box[1]>=32 and occupied>=140 and outside_plus>=30:
            cells.append({'row': 1, 'column': column})
    return cells


def verify_panel(d, w, template, g):
    if ui.signature(w) != template:
        raise RuntimeError('Group title changed')
    root = d.screen().root
    prop = root.get_full_property(d.intern_atom('_NET_ACTIVE_WINDOW'), 0)
    if not prop:
        raise RuntimeError('No active window')
    active = d.create_resource_object('window', int(prop.value[0]))
    origin = root.translate_coords(active.id, 0, 0)
    size = active.get_geometry()
    if ('wechat' not in str(active.get_wm_class()).lower() or
            abs(origin.x - g.x) > 2 or abs(origin.y - g.y) > 2 or
            size.width != g.width or size.height != g.height):
        raise RuntimeError('Native sticker panel is not active')


def set_query(g, query):
    px = lambda value: round(value * ui.desktop_scale())
    keeper = subprocess.Popen(['xclip', '-selection', 'clipboard', '-in', '-quiet'],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        keeper.stdin.write(query.encode())
        keeper.stdin.close()
        time.sleep(.1)
        ui.xdo('mousemove', g.x + px(120), g.y + px(38), 'click', '1')
        ui.xdo('key', '--clearmodifiers', 'ctrl+a', 'ctrl+v')
        time.sleep(.5)
        ui.xdo('mousemove', g.x + px(120), g.y + px(38), 'click', '1')
        ui.xdo('key', '--clearmodifiers', 'ctrl+a', 'ctrl+c')
        time.sleep(.1)
        copied = subprocess.check_output(['xclip', '-selection', 'clipboard', '-o'], timeout=3).decode()
        if copied != query:
            raise RuntimeError('Native search text verification failed')
        # Arrow/Enter are tab navigation shortcuts in this popup, not editor keys.
    finally:
        if keeper.poll() is None:
            keeper.terminate()
            keeper.wait(timeout=3)


def main():
    payload = json.load(sys.stdin)
    group = payload['group_id']
    if not re.fullmatch(r'(?:[0-9]+@chatroom|[A-Za-z0-9_-]{3,128})', group):
        raise ValueError('Explicit group required')
    source = payload.get('source', 'search')
    if source not in ('search', 'favorites'):
        raise ValueError('Invalid sticker source')
    profiles = json.loads((ui.ROOT / 'titles.json').read_text())
    template = profiles[group]['signature']
    d = display.Display(':1')
    scale = ui.desktop_scale(d)
    px = lambda value: round(value * scale)
    w = ui.main_window(d)
    if sys.argv[1] == 'prepare':
        ui.verify(d, w, template)
        if w.get_geometry().width != px(980) or w.get_geometry().height != px(710):
            raise RuntimeError('Unsupported native sticker layout')
        query = payload.get('query', '')
        if not isinstance(query, str) or not 1 <= len(query) <= 30 or any(ord(c) < 32 for c in query):
            raise ValueError('Invalid sticker query')
        ui.xdo('key', 'Escape')
        ui.xdo('mousemove', '--window', w.id, px(300), px(586), 'click', '1')
        time.sleep(.3)
        g = panel(d, w)
        verify_panel(d, w, template, g)
        ui.xdo('mousemove', g.x + px(35 if source == 'search' else 133), g.y + g.height - px(24), 'click', '1')
        time.sleep(.3)
        if source == 'search':
            set_query(g, query)
            # Search is live. Enter changes tabs in this client, so do not press it.
            time.sleep(2)
            ui.xdo('mousemove', '--window', w.id, px(850), px(42))
        else:
            time.sleep(.5)
        g = panel(d, w)
        im = picture(d, g)
        # No screenshot leaves this process; caller receives only occupied slots.
        print(json.dumps({'candidates': visible_cells(im, source),
            'header': header(im, source), 'scale': scale, 'geometry': [g.x, g.y, g.width, g.height]}))
    elif sys.argv[1] == 'send':
        if payload.get('scale', 1) != scale:
            raise RuntimeError('Desktop DPI changed')
        if time.time() - payload['prepared'] > 90:
            raise RuntimeError('Sticker selection expired')
        g = panel(d, w)
        if [g.x, g.y, g.width, g.height] != payload['geometry']:
            raise RuntimeError('Sticker panel moved')
        if header(picture(d, g), source) != payload['header']:
            raise RuntimeError('Sticker query or panel changed')
        verify_panel(d, w, template, g)
        if source == 'search':
            ui.xdo('mousemove', g.x + px(120), g.y + px(38), 'click', '1')
            ui.xdo('key', '--clearmodifiers', 'ctrl+a', 'ctrl+c')
            time.sleep(.1)
            copied = subprocess.check_output(['xclip', '-selection', 'clipboard', '-o'], timeout=3).decode()
            if copied != payload['query']:
                raise RuntimeError('Search keyword changed')
        x, y = payload['point']
        if not (px(20) <= x <= g.width - px(20) and px(115 if source == 'search' else 40) <= y <= g.height - px(65)):
            raise ValueError('Selection is outside sticker content')
        column = round((x / scale - 56) / 88) + 1
        expected_y = px(151 if source == 'search' else 56)
        if (abs(x-px(56+88*(column-1)))>1 or abs(y-expected_y)>1
                or {'row':1,'column':column} not in visible_cells(picture(d,g), source)):
            raise RuntimeError('Selected sticker cell is no longer available')
        verify_panel(d, w, template, g)
        ui.xdo('mousemove', g.x + x, g.y + y, 'click', '1')
        time.sleep(.3)
        ui.xdo('key', 'Escape')
        print(json.dumps({'submitted': True}))
    else:
        raise ValueError('Unknown native sticker action')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
