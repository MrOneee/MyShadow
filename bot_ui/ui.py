"""Constrained X11 reply delivery to a manually verified, currently open group."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from Xlib import display, error
from PIL import Image

ROOT = Path(__file__).resolve().parent
os.environ['DISPLAY'] = ':1'


def xdo(*args):
    return subprocess.check_output(['xdotool', *map(str, args)], text=True, timeout=5).strip()


def desktop_scale(d=None):
    d = d or display.Display(':1')
    prop = d.screen().root.get_full_property(d.intern_atom('RESOURCE_MANAGER'), 0)
    resources = prop.value.decode('utf-8', 'replace') if prop and isinstance(prop.value, bytes) else ''
    match = re.search(r'^Xft\.dpi:\s*([0-9.]+)\s*$', resources, re.M)
    scale = float(match[1]) / 96 if match else 1.0
    if not .75 <= scale <= 3:
        raise RuntimeError('Unsupported desktop DPI')
    return scale


def main_window(d):
    prop = d.screen().root.get_full_property(d.intern_atom('_NET_CLIENT_LIST'), 0)
    candidates = []
    for identifier in prop.value if prop else []:
        w = d.create_resource_object('window', int(identifier))
        if 'wechat' in str(w.get_wm_class()).lower() and w.get_attributes().map_state == 2:
            g = w.get_geometry()
            if g.width >= 800 and g.height >= 600:
                candidates.append(w)
    if len(candidates) != 1:
        raise RuntimeError('Exactly one logged-in WeChat window is required')
    return candidates[0]


def signature(w):
    g = w.get_geometry()
    scale = desktop_scale()
    x, y, margin, height = (round(n * scale) for n in (280, 26, 360, 38))
    # Title strip only. Notification badges and message contents are excluded.
    data = w.get_image(x, y, g.width - margin, height, 2, 0xffffffff)
    img = Image.frombytes('RGB', (g.width - margin, height), data.data, 'raw', 'BGRX')
    return {'width': g.width, 'height': g.height,
            'title_sha256': hashlib.sha256(img.tobytes()).hexdigest()}


def verify(d, w, template):
    if signature(w) != template:
        raise RuntimeError('Group title or window geometry changed; delivery stopped')
    active = d.screen().root.get_full_property(d.intern_atom('_NET_ACTIVE_WINDOW'), 0)
    if not active or int(active.value[0]) != w.id:
        raise RuntimeError('WeChat is not the active window')


def save_profile(path, profiles, group_id, group_name, value):
    for other_id, profile in profiles.items():
        if other_id != group_id and profile.get('signature') == value:
            raise RuntimeError('Selected screen matches another group; profile update refused')
    os.umask(0o077)
    profiles[group_id] = {'group_name': group_name, 'signature': value}
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(profiles, ensure_ascii=False))
    temp.chmod(0o600)
    temp.replace(path)


def paste(text):
    proc = subprocess.Popen(['xclip', '-selection', 'clipboard', '-in', '-quiet'],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.stdin.write(text.encode())
        proc.stdin.close()
        time.sleep(.1)
        xdo('key', '--clearmodifiers', 'ctrl+v')
        time.sleep(.2)
        xdo('key', '--clearmodifiers', 'ctrl+a', 'ctrl+c')
        time.sleep(.1)
        copied = subprocess.check_output(['xclip', '-selection', 'clipboard', '-o'], timeout=3).decode()
        if copied != text:
            raise RuntimeError('Input text verification failed')
        xdo('key', '--clearmodifiers', 'Right')
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=3)


def select_group(w, group_name, unique_result=False, expected_signature=None, direct=False):
    d = display.Display(':1')
    scale = desktop_scale(d)
    px = lambda value: round(value * scale)
    xdo('key', '--clearmodifiers', 'Escape')
    xdo('mousemove', '--window', w.id, px(145), px(42), 'click', '1')
    xdo('key', '--clearmodifiers', 'ctrl+a')
    paste(group_name)
    # Network suggestions vary in count. Locate the actual search popup and
    # choose its local-result row from the bottom, never a fixed screen row.
    root = d.screen().root
    origin = root.translate_coords(w.id, 0, 0)
    deadline = time.monotonic() + 5
    previous, stable_since, popup, popup_shot = None, time.monotonic(), None, None
    while time.monotonic() < deadline:
        popups = []
        for child in root.query_tree().children:
            g = child.get_geometry()
            if (child.get_attributes().map_state == 2 and px(300) <= g.width <= px(500)
                    and px(120) <= g.height <= max(px(600),w.get_geometry().height) and abs(g.x - origin.x - px(74)) <= px(4)
                    and abs(g.y - origin.y - px(58)) <= px(4)):
                popups.append(g)
        shape = None if len(popups) != 1 else (popups[0].x, popups[0].y, popups[0].width, popups[0].height)
        shot = None if shape is None else root.get_image(*shape, 2, 0xffffffff)
        state = None if shot is None else (shape, hashlib.sha256(shot.data).digest())
        if state != previous:
            previous, stable_since = state, time.monotonic()
        if state and time.monotonic() - stable_since >= .8:
            popup = popups[0]
            popup_shot = shot
            break
        time.sleep(.1)
    if popup is None:
        raise RuntimeError('Local group search popup could not be located')
    # The popup can reorder network suggestions around local results. Its first
    # full-size avatar is the exact group result; matching chat-history avatars
    # follow it. Magnifying-glass network rows have no full-size avatar.
    image = Image.frombytes('RGB', (popup.width, popup.height), popup_shot.data, 'raw', 'BGRX')
    avatar_rows = []
    for y in range(popup.height):
        colored = sum(1 for x in range(px(8), min(px(48), popup.width))
                      if sum(255 - channel for channel in image.getpixel((x, y))) > 30)
        if colored >= px(18):
            avatar_rows.append(y)
    regions = []
    for y in avatar_rows:
        if not regions or y > regions[-1][1] + px(3):
            regions.append([y, y])
        else:
            regions[-1][1] = y
    avatars = [region for region in regions if region[0] > px(30) and region[1] - region[0] >= px(24)]
    if not avatars:
        raise RuntimeError('Exact local group result could not be identified')
    if direct:
        # Contacts occupy the first section; matching groups and history follow.
        # The host has verified a unique contact name before invoking this path.
        avatars=[region for region in avatars if region[0]<px(100)]
        if len(avatars)!=1:raise RuntimeError('Unique first contact result unavailable')
    # Named groups are not always keyboard-selected either. Select the single
    # stable local result explicitly; never activate an unknown keyboard row.
    ambiguous = len(avatars) != 1
    if ambiguous and expected_signature is None:
        raise RuntimeError('Group search is ambiguous')
    current = root.get_image(popup.x, popup.y, popup.width, popup.height, 2, 0xffffffff)
    if current.data != popup_shot.data:
        raise RuntimeError('Search results changed before selection')
    # The avatar opens a preview, so double-click its adjacent label instead.
    xdo('mousemove', popup.x + px(124), popup.y + sum(avatars[0]) // 2,
        'click', '--repeat', 2, '--delay', 120, '1')
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        visible = False
        for child in root.query_tree().children:
            try:
                current = child.get_geometry()
                if (child.get_attributes().map_state == 2 and current.x == popup.x
                        and current.y == popup.y and current.width == popup.width
                        and current.height == popup.height):
                    visible = True
                    break
            except error.XError:
                continue
        if not visible:
            break
        time.sleep(.1)
    else:
        raise RuntimeError('Exact group result did not open')
    time.sleep(.4)
    # Search can include chat-history avatars beneath the group result. Only a
    # previously verified title may authorize that selection; never refresh a
    # profile from an ambiguous result, even if the caller allows refresh.
    if ambiguous and signature(w) != expected_signature:
        raise RuntimeError('Ambiguous result did not match the verified group title')


def main():
    action = sys.argv[1]
    payload = {} if action == 'logged-in' else json.load(sys.stdin)
    if action != 'logged-in':
        group_id = payload.get('group_id', '')
        group_name = payload.get('group_name', '')
        private = payload.get('conversation_type') == 'direct'
        valid_id = bool(re.fullmatch(r'[A-Za-z0-9_-]{3,128}',group_id)) if private else bool(re.fullmatch(r'[0-9]+@chatroom',group_id))
        if not valid_id or not group_name.strip():
            raise ValueError('Explicit group ID and name are required')
    profiles_path = ROOT / 'titles.json'
    d = display.Display(':1')
    w = main_window(d)
    if action == 'logged-in':
        print('WeChat is logged in')
        return
    xdo('windowactivate', '--sync', w.id)
    time.sleep(.1)
    if action == 'capture-title':
        profiles = json.loads(profiles_path.read_text()) if profiles_path.exists() else {}
        save_profile(profiles_path, profiles, group_id, group_name, signature(w))
        print('Title verification template saved')
        return
    if action == 'open':
        select_group(w, group_name, payload.get('select_unique_result', False), direct=private)
        print('Group candidate opened; visual verification required')
        return
    profiles = json.loads(profiles_path.read_text()) if profiles_path.exists() else {}
    profile = profiles.get(group_id)
    if action == 'ready':
        for attempt in range(2):
            if profile and profile['group_name'] == group_name and signature(w) == profile['signature']:
                break
            select_group(w, group_name, payload.get('select_unique_result', False),
                         profile['signature'] if profile and profile['group_name'] == group_name else None, direct=private)
            deadline = time.monotonic() + 1.5
            previous, stable_since = None, time.monotonic()
            while time.monotonic() < deadline:
                current = signature(w)
                if current != previous:
                    previous, stable_since = current, time.monotonic()
                if time.monotonic() - stable_since >= .3:
                    break
                time.sleep(.1)
            if profile and profile['group_name'] == group_name and current == profile['signature']:
                break
            if payload.get('allow_profile_refresh'):
                save_profile(profiles_path, profiles, group_id, group_name, current)
                profile = profiles[group_id]
                break
        if not profile or profile['group_name'] != group_name:
            raise RuntimeError('Unverified target group')
        verify(d, w, profile['signature'])
        print('Group title verified')
        return
    if not profile or profile['group_name'] != group_name:
        raise RuntimeError('Unverified target group')
    template = profile['signature']
    verify(d, w, template)
    if action == 'check':
        print('Group title verified')
        return
    if action not in ('draft-check', 'send'):
        raise ValueError('Unknown action')
    text = payload['text']
    if not isinstance(text, str) or not text.strip() or len(text) > 5000:
        raise ValueError('Invalid reply text')
    g = w.get_geometry()
    xdo('mousemove', '--window', w.id, g.width // 2, g.height - round(80 * desktop_scale(d)), 'click', '1')
    xdo('key', '--clearmodifiers', 'ctrl+a')
    # Do not overwrite a human draft: selecting/copying an empty editor leaves
    # clipboard unchanged, so use a fresh sentinel before Ctrl+C.
    sentinel = '__WEIXIN_EMPTY_EDITOR__'
    keeper = subprocess.Popen(['xclip', '-selection', 'clipboard', '-in', '-quiet'],
                              stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        keeper.stdin.write(sentinel.encode()); keeper.stdin.close()
        time.sleep(.1)
        xdo('key', '--clearmodifiers', 'ctrl+c')
        time.sleep(.1)
        old = subprocess.check_output(['xclip', '-selection', 'clipboard', '-o'], timeout=3).decode()
        if old not in ('', sentinel):
            raise RuntimeError('Input already contains a draft; delivery stopped')
    finally:
        if keeper.poll() is None:
            keeper.terminate(); keeper.wait(timeout=3)
    paste(text)
    verify(d, w, template)
    if action == 'send':
        xdo('key', '--clearmodifiers', 'Return')
        print('Reply submitted')
    else:
        xdo('key', '--clearmodifiers', 'ctrl+a', 'BackSpace')
        print('Draft verified and cleared; no message sent')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
