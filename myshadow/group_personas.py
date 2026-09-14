"""Trusted, immutable persona profiles; never selected by message text."""
import re
from types import MappingProxyType

DEFAULT_PROFILE = '__default__'
PROFILE_ID = re.compile(r'[A-Za-z0-9_.-]{1,40}')


def _inside(root, relative, label):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(label + ' must be inside the project')
    return path


def _read(root, relative, label, limit=20000):
    text = _inside(root, relative, label).read_text(encoding='utf-8').strip()
    if not text or len(text) > limit:
        raise ValueError('Invalid ' + label.lower() + ' length')
    return text


def _profile(config, root, entry, persona_id=''):
    if not isinstance(entry, dict):
        raise ValueError('Invalid persona profile')
    name = entry.get('name', '')
    aliases = entry.get('aliases', [name])
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 20
            or not isinstance(aliases, list) or not aliases
            or not all(isinstance(a, str) and 1 <= len(a.strip()) <= 20 for a in aliases)):
        raise ValueError('Invalid persona name or aliases')
    parts = []
    if entry.get('identity_file'):
        parts.append(_read(root, entry['identity_file'], 'Persona identity'))
        if entry.get('voice_file'):
            parts.append(_read(root, entry['voice_file'], 'Persona voice'))
        policy = entry.get('policy_file', config.get('persona_policy_file'))
        if policy:
            parts.append(_read(root, policy, 'Persona policy'))
    elif entry.get('prompt_file'):
        parts.append(_read(root, entry['prompt_file'], 'Persona prompt'))
    else:
        raise ValueError('Persona profile requires identity_file or prompt_file')
    prompt = '\n\n'.join(parts)
    if len(prompt) > 20000:
        raise ValueError('Invalid assembled persona prompt length')
    core = ''
    background = entry.get('background', '')
    if background:
        directory = _inside(root, background, 'Background directory')
        core = _read(directory, 'core.md', 'Background core', 4000)
        background = str(directory.relative_to(root.resolve())).replace('\\', '/')
    budget = entry.get('context_token_budget', config.get('context_token_budget', 12800))
    if type(budget) is not int or not 2048 <= budget <= 64000:
        raise ValueError('Invalid group context budget')
    style = entry.get('style', {})
    if not isinstance(style, dict):
        raise ValueError('Invalid persona style')
    for key in ('split_probability', 'three_bubble_probability', 'sticker_probability'):
        if key in style and (type(style[key]) not in (int, float) or not 0 <= style[key] <= 1):
            raise ValueError('Invalid persona style probability')
    if ('casual_target_chars' in style and
            (not isinstance(style['casual_target_chars'], str) or len(style['casual_target_chars']) > 40)):
        raise ValueError('Invalid persona style target')
    for key in ('natural_markers', 'sticker_intents'):
        if key in style and (not isinstance(style[key], list) or len(style[key]) > 12 or
                not all(isinstance(value, str) and 1 <= len(value) <= 12 for value in style[key])):
            raise ValueError('Invalid persona style words')
    frozen_style = {key: tuple(value) if isinstance(value, list) else value for key,value in style.items()}
    return MappingProxyType({'persona_id': persona_id, 'name': name,
        'aliases': tuple(dict.fromkeys([name] + aliases)), 'system_prompt': prompt,
        'background': background, 'background_core': core,
        'context_token_budget': budget, 'style': MappingProxyType(frozen_style)})


def load_personas(config, root):
    result = {}
    definitions = config.get('persona_profiles', {})
    if not isinstance(definitions, dict):
        raise ValueError('Invalid persona profiles')
    loaded = {}
    for persona_id, entry in definitions.items():
        if not isinstance(persona_id, str) or not PROFILE_ID.fullmatch(persona_id):
            raise ValueError('Invalid persona id')
        loaded[persona_id] = _profile(config, root, entry, persona_id)
    default_id = config.get('default_persona')
    if default_id is not None:
        if default_id not in loaded:
            raise ValueError('Unknown default persona')
        result[DEFAULT_PROFILE] = loaded[default_id]
    for group, entry in config.get('group_personas', {}).items():
        if not re.fullmatch(r'[0-9]+@chatroom', group) or not isinstance(entry, dict):
            raise ValueError('Invalid group persona')
        persona_id = entry.get('persona_id')
        if persona_id:
            if persona_id not in loaded:
                raise ValueError('Unknown group persona')
            merged = dict(definitions[persona_id])
            merged.update({key: value for key, value in entry.items() if key != 'persona_id'})
            result[group] = _profile(config, root, merged, persona_id)
        else:
            result[group] = _profile(config, root, entry)
    return MappingProxyType(result)


def resolve(config, profiles, group):
    profile = profiles.get(group)
    if profile is not None:
        return profile
    profile = profiles.get(DEFAULT_PROFILE)
    if profile is not None:
        return profile
    name = config.get('bot_name', '影')
    return {'name': name, 'aliases': tuple(config.get('selective_reply', {}).get('aliases', [name])),
            'system_prompt': config.get('system_prompt', ''), 'persona_id': '', 'style': {}}
