"""Trusted, immutable group identity configuration; never selected by message text."""
import re
from types import MappingProxyType


def load_personas(config, root):
    result = {}
    for group, entry in config.get('group_personas', {}).items():
        if not re.fullmatch(r'[0-9]+@chatroom', group) or not isinstance(entry, dict):
            raise ValueError('Invalid group persona')
        name = entry.get('name', '')
        aliases = entry.get('aliases', [name])
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 20
                or not isinstance(aliases, list) or not aliases
                or not all(isinstance(a, str) and 1 <= len(a.strip()) <= 20 for a in aliases)):
            raise ValueError('Invalid persona name or aliases')
        path = (root / entry['prompt_file']).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Persona prompt must be inside the project')
        prompt = path.read_text(encoding='utf-8').strip()
        if not prompt or len(prompt) > 20000:
            raise ValueError('Invalid persona prompt length')
        core = ''
        budget = entry.get('context_token_budget', config.get('context_token_budget', 12800))
        if type(budget) is not int or not 2048 <= budget <= 64000:
            raise ValueError('Invalid group context budget')
        if entry.get('background'):
            core_path = (root / entry['background'] / 'core.md').resolve()
            if not core_path.is_relative_to(root.resolve()):
                raise ValueError('Background core must be inside the project')
            core = core_path.read_text(encoding='utf-8').strip()
            if not core or len(core) > 4000:
                raise ValueError('Invalid background core length')
        result[group] = MappingProxyType({'name': name, 'aliases': tuple(dict.fromkeys([name] + aliases)),
                                         'system_prompt': prompt, 'background_core': core, 'context_token_budget': budget})
    return MappingProxyType(result)


def resolve(config, profiles, group):
    profile = profiles.get(group)
    if profile is not None:
        return profile
    name = config.get('bot_name', '影')
    return {'name': name, 'aliases': tuple(config.get('selective_reply', {}).get('aliases', [name])),
            'system_prompt': config.get('system_prompt', '')}
