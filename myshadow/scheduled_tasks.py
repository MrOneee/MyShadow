"""Small persistent scheduler. No threads; the bot's existing poll drives due work."""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')

SCHEDULE_TOOL = {'type': 'function', 'function': {
    'name': 'manage_schedule',
    'description': '仅当前会话中好感度超过门槛的成员管理自己的任务。北京时间。create/update需完整任务；update替换指定任务。按内容或时间修改/取消前先list匹配，多个匹配先澄清。task_id仅内部调用使用，不向用户展示或索要。时间不清先问。list查询，cancel取消。',
    'parameters': {'type': 'object', 'additionalProperties': False,
        'properties': {
            'operation': {'type': 'string', 'enum': ['create', 'list', 'update', 'cancel']},
            'task_id': {'type': 'string', 'description': '工具返回的内部标识；不能编造，不展示给用户，不要求用户提供'},
            'kind': {'type': 'string', 'enum': ['once', 'daily', 'weekly', 'interval']},
            'run_at': {'type': 'string', 'description': 'once必填，ISO日期时间，如2026-09-09T09:00:00+08:00'},
            'time': {'type': 'string', 'description': 'daily/weekly必填，HH:MM北京时间'},
            'weekdays': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'weekly必填，周一0到周日6'},
            'interval_minutes': {'type': 'integer', 'description': 'interval必填，至少5分钟，从创建时起算'},
            'action': {'type': 'string', 'enum': ['remind', 'ask_ai']},
            'text': {'type': 'string', 'description': '提醒正文或到时执行的AI问题，最多500字；AI可搜索/查天气，无额外后台能力'}},
        'required': ['operation']}}}


def schedule_intent(text):
    return bool(re.search(r'提醒|定时|周期|任务|每天|每日|每周|每隔|分钟后|小时后|明天.*点|取消|改到|改成|第[一二三四五六七八九十0-9]+条', text))


def spec_from(args, now):
    kind = args.get('kind')
    spec = {'kind': kind}
    if kind == 'once':
        value = args.get('run_at', '')
        if not isinstance(value, str) or not re.match(r'^\d{4}-\d\d-\d\dT\d\d:\d\d', value):
            raise ValueError('需要完整的日期和时间')
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        due = int(dt.timestamp())
        if due <= now or due > now + 366 * 86400:
            raise ValueError('单次时间须在未来一年内')
        spec['run_at'] = due
    elif kind in ('daily', 'weekly'):
        value = args.get('time')
        if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('时间须为北京时间HH:MM')
        spec['time'] = value
        if kind == 'weekly':
            days = args.get('weekdays')
            if not isinstance(days, list) or not days or len(days) > 7 or any(type(d) is not int or not 0 <= d <= 6 for d in days):
                raise ValueError('星期须为0到6的列表')
            spec['weekdays'] = sorted(set(days))
    elif kind == 'interval':
        minutes = args.get('interval_minutes')
        if type(minutes) is not int or not 5 <= minutes <= 525600:
            raise ValueError('间隔须为5到525600分钟')
        spec.update(interval_minutes=minutes, anchor=now)
    else:
        raise ValueError('不支持的周期')
    return spec


def next_due(spec, after):
    if spec['kind'] == 'once':
        return spec['run_at'] if spec['run_at'] > after else None
    if spec['kind'] == 'interval':
        seconds = spec['interval_minutes'] * 60
        anchor = spec['anchor']
        return anchor + max(1, (after - anchor) // seconds + 1) * seconds
    now = datetime.fromtimestamp(after, TZ)
    hour, minute = map(int, spec['time'].split(':'))
    for offset in range(8):
        candidate = (now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.timestamp() > after and (spec['kind'] == 'daily' or candidate.weekday() in spec['weekdays']):
            return int(candidate.timestamp())
    raise ValueError('找不到下一次执行时间')


class ScheduledTasks:
    def __init__(self, state, affinity_for, min_affinity=50):
        if not callable(affinity_for):
            raise TypeError('scheduler affinity callback is required')
        if type(min_affinity) not in (int,float) or not 0<=min_affinity<100:
            raise ValueError('scheduler minimum affinity must be from 0 to below 100')
        self.state, self.affinity_for, self.min_affinity = state, affinity_for, min_affinity
        state.executescript('''
            CREATE TABLE IF NOT EXISTS scheduled_tasks(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, group_id TEXT NOT NULL,
                spec TEXT NOT NULL, action TEXT NOT NULL, text TEXT NOT NULL,
                next_due INTEGER, status TEXT NOT NULL, created INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS scheduled_due ON scheduled_tasks(status,next_due);
            CREATE TABLE IF NOT EXISTS scheduled_runs(
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, due INTEGER NOT NULL,
                status TEXT NOT NULL, reply_id INTEGER, UNIQUE(task_id,due));
            CREATE TABLE IF NOT EXISTS schedule_operations(
                key TEXT PRIMARY KEY, result TEXT NOT NULL, created INTEGER NOT NULL);
        ''')

    def authorized(self, sender, group):
        try:
            score=self.affinity_for(group,sender)
            return type(score) in (int,float) and score>self.min_affinity
        except (OSError, ValueError, TypeError, sqlite3.Error):
            return False

    def manage(self, sender, group, source, args, now=None):
        if not self.authorized(sender, group):
            return {'error': '当前会话好感度需要超过'+format(self.min_affinity,'g')+'才能管理定时任务'}
        now = int(time.time() if now is None else now)
        if not isinstance(args, dict) or set(args) - set(SCHEDULE_TOOL['function']['parameters']['properties']):
            return {'error': '无效参数'}
        op = args.get('operation')
        if op == 'list':
            rows = self.state.execute('SELECT * FROM scheduled_tasks WHERE owner=? AND group_id=? ORDER BY created DESC LIMIT 30', (sender, group)).fetchall()
            return {'timezone': 'Asia/Shanghai', 'tasks': [self.describe(r) for r in rows]}
        if op not in ('create', 'update', 'cancel'):
            return {'error': '不支持的操作'}
        key = hashlib.sha256(json.dumps([sender, group, source, args], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        previous = self.state.execute('SELECT result FROM schedule_operations WHERE key=?', (key,)).fetchone()
        if previous:
            return json.loads(previous[0])
        try:
            with self.state:
                ident = args.get('task_id')
                if op != 'create':
                    if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{12}', ident):
                        raise ValueError('请提供任务列表中的task_id')
                    task = self.state.execute('SELECT * FROM scheduled_tasks WHERE id=? AND owner=? AND group_id=?', (ident, sender, group)).fetchone()
                    if task is None:
                        raise ValueError('本群没有这个任务，请先查询任务列表')
                if op == 'cancel':
                    self.state.execute("UPDATE scheduled_tasks SET status='cancelled',next_due=NULL WHERE id=?", (ident,))
                    result = {'status': 'cancelled', 'task_id': ident}
                else:
                    text, action = args.get('text'), args.get('action')
                    if not isinstance(text, str) or not text.strip() or len(text) > 500 or action not in ('remind', 'ask_ai'):
                        raise ValueError('需要1到500字的任务内容及有效action')
                    spec = spec_from(args, now)
                    if op == 'create':
                        count = self.state.execute("SELECT count(*) FROM scheduled_tasks WHERE owner=? AND status='active'", (sender,)).fetchone()[0]
                        if count >= 20:
                            raise ValueError('最多同时启用20个任务')
                        ident = uuid.uuid4().hex[:12]
                    elif task['status'] != 'active':
                        raise ValueError('只能修改启用中的任务；请新建任务')
                    self.state.execute('INSERT OR REPLACE INTO scheduled_tasks VALUES(?,?,?,?,?,?,?,?,?)',
                        (ident, sender, group, json.dumps(spec), action, text.strip(), next_due(spec, now), 'active', now))
                    result = self.describe(self.state.execute('SELECT * FROM scheduled_tasks WHERE id=?', (ident,)).fetchone())
                    result['result'] = 'created' if op == 'create' else 'updated'
                self.state.execute('INSERT INTO schedule_operations VALUES(?,?,?)', (key, json.dumps(result, ensure_ascii=False), now))
            return result
        except (ValueError, TypeError, OverflowError) as exc:
            return {'error': str(exc)}

    def describe(self, row):
        last = self.state.execute('SELECT status,due FROM scheduled_runs WHERE task_id=? ORDER BY due DESC LIMIT 1', (row['id'],)).fetchone()
        return {'task_id': row['id'], 'status': row['status'], 'action': row['action'], 'text': row['text'],
                'schedule': json.loads(row['spec']), 'timezone': 'Asia/Shanghai',
                'next_run': datetime.fromtimestamp(row['next_due'], TZ).isoformat() if row['next_due'] else None,
                'last_run_status': last['status'] if last else None}

    def recover(self):
        # Never repeat an occurrence interrupted between claiming and UI confirmation.
        with self.state:
            for run in self.state.execute("SELECT * FROM scheduled_runs WHERE status='running'").fetchall():
                reply = self.state.execute('SELECT status FROM replies WHERE id=? AND shard=?', (run['reply_id'], '_scheduled_' + run['id'])).fetchone()
                status = reply['status'] if reply and reply['status'] == 'confirmed' else 'interrupted_uncertain'
                self.state.execute('UPDATE scheduled_runs SET status=? WHERE id=?', (status, run['id']))
                if reply and reply['status'] in ('ready', 'preview'):
                    self.state.execute("UPDATE replies SET status='expired' WHERE id=?", (run['reply_id'],))

    def claim(self, groups, now=None):
        now = int(time.time() if now is None else now)
        with self.state:
            for row in self.state.execute("SELECT * FROM scheduled_tasks WHERE status='active' AND next_due<=? ORDER BY next_due LIMIT 20", (now,)).fetchall():
                if row['group_id'] not in groups:
                    continue
                due = row['next_due']
                following = next_due(json.loads(row['spec']), now)
                self.state.execute('UPDATE scheduled_tasks SET next_due=?,status=? WHERE id=?',
                    (following, 'active' if following else 'completed', row['id']))
                ident = uuid.uuid4().hex
                status = 'running' if now - due <= 300 else 'missed'
                self.state.execute('INSERT INTO scheduled_runs VALUES(?,?,?,?,NULL)', (ident, row['id'], due, status))
                if status == 'running':
                    return dict(row, run_id=ident, due=due)
        return None

    def finish(self, run_id, status):
        with self.state:
            self.state.execute('UPDATE scheduled_runs SET status=? WHERE id=?', (status, run_id))
