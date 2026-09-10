"""Offline event replay and state audit, never invokes models or sends messages."""
import json
from .contracts import Turn


def audit_session(db,registry,session_id):
    session=db.execute('SELECT * FROM activity_sessions WHERE id=?',(session_id,)).fetchone()
    if session is None:raise ValueError('Unknown session')
    skill=registry.get(session['skill'])
    events=db.execute('SELECT * FROM activity_events WHERE session_id=? ORDER BY version',(session_id,)).fetchall()
    public=private=None
    seen=set()
    for expected,row in enumerate(events,1):
        if row['version']!=expected or row['event_key'] in seen:raise ValueError('Event ordering or deduplication violation')
        seen.add(row['event_key'])
        turn=Turn(**json.loads(row['transition']))
        skill.validate_state(turn.public,turn.private)
        public,private=turn.public,turn.private
        actual=db.execute('SELECT count(*) FROM activity_outbox WHERE session_id=? AND version=? AND ordinal<8',(session_id,expected)).fetchone()[0]
        if actual!=len(turn.messages):raise ValueError('Transactional outbox mismatch')
    if len(events)!=session['version'] or public!=json.loads(session['public']) or private!=json.loads(session['private']):
        raise ValueError('Snapshot differs from replayed event state')
    return {'session_id':session_id,'skill':session['skill'],'events':len(events),'phase':session['phase'],'public':public,'valid':True}
