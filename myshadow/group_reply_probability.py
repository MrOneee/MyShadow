"""Live, per-group sampling for otherwise eligible unsolicited replies."""
import json
import math
import random
import threading
from pathlib import Path


class GroupReplyProbability:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._last_text = None
        self._error = None
        self._default, self._groups = 1.0, {}

    def probability(self, group):
        with self._lock:
            try:
                try:
                    with self.path.open(encoding='utf-8') as stream:
                        text = stream.read(65537)
                except FileNotFoundError:
                    self._default, self._groups = 1.0, {}
                    self._last_text, self._error = None, None
                    return self._default
                if text == self._last_text:
                    return self._groups.get(group, self._default)
                if len(text) > 65536:
                    raise ValueError('Settings exceed 64K characters')
                value = json.loads(text)
                if not isinstance(value, dict) or set(value) - {'default_probability', 'groups'}:
                    raise ValueError('Expected default_probability and groups')
                default, groups = value.get('default_probability', 1.0), value.get('groups', {})
                if not isinstance(groups, dict) or not all(
                        isinstance(key, str) and key.endswith('@chatroom') for key in groups):
                    raise ValueError('Expected group IDs ending in @chatroom')
                if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1
                       for p in [default, *groups.values()]):
                    raise ValueError('Probabilities must be finite numbers between 0 and 1')
                self._default, self._groups = default, groups
                self._last_text, self._error = text, None
            except (OSError, ValueError, TypeError, OverflowError) as exc:
                error = type(exc).__name__ + ': ' + str(exc)
                if error != self._error:
                    print(json.dumps({'event': 'group_reply_settings_error', 'error': error}), flush=True)
                    self._error = error
            return self._groups.get(group, self._default)

    def allows(self, group):
        probability = self.probability(group)
        allowed = probability >= 1 or (probability > 0 and random.random() < probability)
        if not allowed:
            print(json.dumps({'event': 'group_reply_probability_skip', 'group_id': group,
                              'probability': probability}), flush=True)
        return allowed
