"""Context-sensitive reply rhythm shared by every persona profile."""
import hashlib
import re

BUBBLE_SEPARATOR = '\n<|next_bubble|>\n'
_SERIOUS = re.compile(r'难受|伤心|焦虑|害怕|生病|吵架|冲突|求助|报错|失败|风险|隐私|安全|确认|核验')
_DETAILED = re.compile(r'详细|具体|完整|方案|教程|步骤|分析|对比|代码|架构|为什么|怎么实现')


def style_chance(seed, label):
    value = hashlib.sha256((str(seed) + ':' + label).encode('utf-8')).digest()
    return int.from_bytes(value[:8], 'big') / 2**64


def reply_style(profile, prompt, seed):
    """Return a deterministic per-turn plan; configuration supplies variation."""
    settings = dict(profile.get('style', {}))
    serious = bool(_SERIOUS.search(prompt))
    detailed = len(prompt) > 120 or bool(_DETAILED.search(prompt))
    target = settings.get('casual_target_chars', '4到20字')
    bubbles = 1
    if not serious and not detailed and style_chance(seed, 'split') < float(settings.get('split_probability', 0)):
        bubbles = 2
        if style_chance(seed, 'third') < float(settings.get('three_bubble_probability', 0)):
            bubbles = 3
    markers = settings.get('natural_markers', [])
    if not isinstance(markers, (list, tuple)):
        markers = []
    sticker_intents = settings.get('sticker_intents', [])
    if not isinstance(sticker_intents, (list, tuple)):
        sticker_intents = []
    return {'serious': serious, 'detailed': detailed, 'bubbles': bubbles,
            'target': str(target)[:40], 'markers': [str(v)[:12] for v in markers[:10]],
            'sticker_intents': [str(v)[:12] for v in sticker_intents[:8]]}


def style_instruction(plan):
    if plan['detailed']:
        rhythm = '本轮需要完整回答，按内容需要展开，默认作为一条消息发送。'
    elif plan['serious']:
        rhythm = '本轮语气认真，先回应具体问题，作为一条消息发送，不用笑声或表情回避。'
    else:
        rhythm = '本轮是日常聊天，目标长度%s，计划发送%d条短消息。' % (plan['target'], plan['bubbles'])
    marker = '、'.join(plan['markers'])
    stickers = '、'.join(plan['sticker_intents'])
    return ('\n【本轮表达节奏】' + rhythm +
        ('可自然选用这些口语连接词，但不能机械套用：' + marker + '。' if marker else '') +
        ('若本轮开放表情工具，偏好的表情意图是：' + stickers + '；仍以具体语境为准。' if stickers else '') +
        ('若确实分成多条，只在相邻消息之间原样输出<|next_bubble|>，不要加序号；必须恰好%d条。' % plan['bubbles']
         if plan['bubbles'] > 1 else '不要输出分条控制标记。'))


def reply_bubbles(text):
    if not isinstance(text, str):
        return []
    parts = [part.strip() for part in re.split(r'\s*<\|next_bubble\|>\s*', text)]
    if not all(parts) or len(parts) > 3:
        cleaned = text.replace('<|next_bubble|>', '\n').strip()
        return [cleaned] if cleaned else []
    return parts


def readable_reply(text):
    return '\n'.join(reply_bubbles(text))
