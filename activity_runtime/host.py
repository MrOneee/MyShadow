"""Durable inbox -> versioned state/event + transactional reply outbox."""
import json
import time
import uuid
from .contracts import Message, Turn, bounded_json
from .services import Contents, Context


class Host:
    def __init__(self,db,registry,model,search=None,admin_ids=(),mode='send',on_progress=None,persona=None):
        self.db,self.registry,self.model,self.search=db,registry,model,search
        self.admin_ids,self.mode=tuple(admin_ids),mode
        self.on_progress=on_progress
        self.persona=persona
        db.executescript('''
            CREATE TABLE IF NOT EXISTS activity_sessions(
                id TEXT PRIMARY KEY,group_id TEXT NOT NULL,skill TEXT NOT NULL,owner TEXT NOT NULL,
                phase TEXT NOT NULL,version INTEGER NOT NULL,state_version INTEGER NOT NULL,
                public TEXT NOT NULL,private TEXT NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS activity_one_live ON activity_sessions(group_id) WHERE phase!='ended';
            CREATE TABLE IF NOT EXISTS activity_inbox(
                id INTEGER PRIMARY KEY,group_id TEXT NOT NULL,event_key TEXT NOT NULL,skill TEXT,
                sender TEXT NOT NULL,name TEXT NOT NULL,text TEXT NOT NULL,created INTEGER NOT NULL,
                addressed INTEGER NOT NULL,kind TEXT NOT NULL,shard TEXT,local_id INTEGER,sort_seq INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',attempts INTEGER NOT NULL DEFAULT 0,error TEXT,
                UNIQUE(group_id,event_key));
            CREATE INDEX IF NOT EXISTS activity_pending ON activity_inbox(group_id,status,id);
            CREATE TABLE IF NOT EXISTS activity_events(
                id INTEGER PRIMARY KEY,session_id TEXT NOT NULL,event_key TEXT NOT NULL,
                version INTEGER NOT NULL,event TEXT NOT NULL,transition TEXT NOT NULL,created INTEGER NOT NULL,
                UNIQUE(session_id,event_key),UNIQUE(session_id,version));
            CREATE TABLE IF NOT EXISTS activity_outbox(
                reply_id INTEGER PRIMARY KEY,session_id TEXT NOT NULL,version INTEGER NOT NULL,
                inbox_id INTEGER NOT NULL,ordinal INTEGER NOT NULL,UNIQUE(inbox_id,ordinal));
            CREATE TABLE IF NOT EXISTS activity_timers(
                session_id TEXT NOT NULL,name TEXT NOT NULL,version INTEGER NOT NULL,due INTEGER NOT NULL,
                payload TEXT NOT NULL,status TEXT NOT NULL,PRIMARY KEY(session_id,name));
        ''')
        if 'sort_seq' not in {r[1] for r in db.execute('PRAGMA table_info(activity_inbox)')}:
            db.execute('ALTER TABLE activity_inbox ADD COLUMN sort_seq INTEGER NOT NULL DEFAULT 0')
            db.commit()
        self.contents=Contents(db)

    def recover(self):
        # Inference has no outward effects; state and outbox commit atomically.
        with self.db:
            self.db.execute("UPDATE activity_inbox SET status='queued' WHERE status='processing'")
            self.db.execute("UPDATE replies SET status='delivery_uncertain' WHERE status IN ('sending','submitted') AND id IN (SELECT reply_id FROM activity_outbox)")

    def current(self,group):
        row=self.db.execute("SELECT * FROM activity_sessions WHERE group_id=? AND phase!='ended'",(group,)).fetchone()
        return dict(row) if row else None

    def reserved(self,group):
        return self.current(group) is not None or self.db.execute("SELECT 1 FROM activity_inbox WHERE group_id=? AND skill IS NOT NULL AND status IN ('queued','processing')",(group,)).fetchone() is not None

    def ingest(self,group,event,shard='',local_id=0):
        if not event.text or not event.sender:return False
        match=self.registry.match(event.text)
        if not self.reserved(group) and not match:return False
        self.db.execute('INSERT OR IGNORE INTO activity_inbox(group_id,event_key,skill,sender,name,text,created,addressed,kind,shard,local_id,sort_seq) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (group,event.key,match,event.sender,event.name,event.text[:4000],event.created,int(event.addressed),event.kind,shard,local_id,event.sort_seq))
        return True

    def tick(self,groups,now=None):
        now=int(time.time() if now is None else now)
        with self.db:
            for row in self.db.execute("SELECT t.*,s.group_id,s.phase,s.version AS current_version FROM activity_timers t JOIN activity_sessions s ON s.id=t.session_id WHERE t.status='waiting' AND due<=?",(now,)).fetchall():
                if row['group_id'] not in groups:continue
                valid=row['phase']=='active' and row['version']==row['current_version'] and now-row['due']<600
                if valid:
                    self.ingest(row['group_id'],Message('timer:'+row['session_id']+':'+row['name']+':'+str(row['version']),
                        '_timer','',row['payload'],now,False,'timer'))
                self.db.execute('UPDATE activity_timers SET status=? WHERE session_id=? AND name=?',
                    ('queued' if valid else 'cancelled',row['session_id'],row['name']))

    def context(self,skill,session,event):
        context=Context(skill,self.model,self.search,self.contents,session,event,self.admin_ids)
        context.persona=self.persona(session['group_id']) if self.persona else {}
        context.previous_public=[json.loads(r[0]) for r in self.db.execute(
            "SELECT public FROM activity_sessions WHERE group_id=? AND skill=? AND phase='ended' ORDER BY updated DESC LIMIT 30",
            (session['group_id'],session['skill']))]
        return context

    def _reply(self,session,inbox,text,version,ordinal):
        if not isinstance(text,str) or not text.strip() or len(text)>5000:raise ValueError('Invalid activity message')
        local=inbox['id']*10+ordinal
        cur=self.db.execute('INSERT INTO replies(group_id,shard,local_id,created,prompt,reply,status) VALUES(?,?,?,?,?,?,?)',
            (session['group_id'],'_activity',local,int(time.time()),inbox['text'],text,'ready' if self.mode=='send' else 'preview'))
        self.db.execute('INSERT INTO activity_outbox VALUES(?,?,?,?,?)',(cur.lastrowid,session['id'],version,inbox['id'],ordinal))

    def process(self,group,limit=6):
        processed=0
        for row in self.db.execute("SELECT * FROM activity_inbox WHERE group_id=? AND status='queued' ORDER BY created,sort_seq,id LIMIT ?",(group,limit)).fetchall():
            session=self.current(group)
            if session is None:
                if not row['skill']:
                    self._unhandled(row);continue
                skill=self.registry.get(row['skill'])
                now=int(time.time())
                session={'id':uuid.uuid4().hex,'group_id':group,'skill':row['skill'],'owner':row['sender'],
                    'phase':'new','version':0,'state_version':skill.manifest['state_version'],
                    'public':'{}','private':'{}','created':now,'updated':now}
            else:
                skill=self.registry.get(session['skill'])
            if session['state_version']!=skill.manifest['state_version']:
                raise RuntimeError('Activity state version requires a package migration')
            event=Message(row['event_key'],row['sender'],row['name'],row['text'],row['created'],bool(row['addressed']),row['kind'],row['sort_seq'])
            with self.db:
                claimed=self.db.execute("UPDATE activity_inbox SET status='processing',attempts=attempts+1 WHERE id=? AND status='queued'",(row['id'],)).rowcount
            if not claimed:continue
            context=self.context(skill,session,event)
            context._report=lambda text: self._progress(session,row,text)
            try:
                turn=skill.handle(context,json.loads(session['public']),json.loads(session['private']),event)
                if not isinstance(turn,Turn):raise ValueError('Skill must return a Turn')
                if not turn.handled:
                    self._unhandled(row);continue
                self._validate(skill,context,turn)
                self._commit(session,row,event,turn)
                processed+=1
            except Exception as exc:
                # No invented answer or progress; the same real question can be asked again.
                with self.db:
                    self.db.execute("UPDATE activity_inbox SET status='failed',error=? WHERE id=?",(type(exc).__name__+': '+str(exc)[:300],row['id']))
                    if row['kind']!='timer':
                        self._reply(session,row,skill.manifest.get('failure_message','本次请求处理失败，状态未更新，请稍后重试。'),session['version'],9)
                print(json.dumps({'event':'activity_error','group_id':group,'inbox_id':row['id'],'type':type(exc).__name__,'error':str(exc)[:300]}),flush=True)
                break
        return processed

    def _progress(self,session,row,text):
        # One durable interim update per input; replay never duplicates it.
        if self.db.execute('SELECT 1 FROM activity_outbox WHERE inbox_id=? AND ordinal=8',(row['id'],)).fetchone():return
        with self.db:self._reply(session,row,text,session['version'],8)
        reply_id=self.db.execute('SELECT reply_id FROM activity_outbox WHERE inbox_id=? AND ordinal=8',(row['id'],)).fetchone()[0]
        if self.mode=='send' and self.on_progress:self.on_progress(reply_id)

    def _unhandled(self,row):
        with self.db:
            if row['addressed'] and row['shard']:
                self.db.execute('INSERT OR IGNORE INTO replies(group_id,shard,local_id,created,prompt,status) VALUES(?,?,?,?,?,?)',
                    (row['group_id'],row['shard'],row['local_id'],row['created'],row['text'],'pending'))
            self.db.execute("UPDATE activity_inbox SET status='ignored' WHERE id=?",(row['id'],))

    def _validate(self,skill,context,turn):
        if turn.phase not in ('active','paused','ended'):raise ValueError('Invalid activity phase')
        if len(turn.messages)>3 or len(turn.timers)>4:raise ValueError('Too many activity effects')
        if turn.messages and 'send' not in skill.manifest['capabilities']:raise PermissionError('Send capability unavailable')
        if turn.timers and 'timers' not in skill.manifest['capabilities']:raise PermissionError('Timer capability unavailable')
        bounded_json(turn.public);bounded_json(turn.private);bounded_json(turn.audit)
        if not context.permitted(turn.action):raise PermissionError('Activity action forbidden')
        if turn.audience not in ('public','reveal'):raise ValueError('Invalid message audience')
        if turn.audience=='reveal' and not skill.can_publish(context,turn):raise PermissionError('Disclosure condition not met')
        skill.validate_state(turn.public,turn.private)
        for item in turn.timers:
            if not isinstance(item.get('name'),str) or not 30<=item.get('delay',0)<=86400:raise ValueError('Invalid activity timer')
            bounded_json(item.get('payload',{}),4000)

    def _commit(self,session,row,event,turn):
        version=session['version']+1;now=int(time.time())
        with self.db:
            if session['version']==0:
                self.db.execute('INSERT INTO activity_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (session['id'],session['group_id'],session['skill'],session['owner'],turn.phase,version,
                     session['state_version'],bounded_json(turn.public),bounded_json(turn.private),now,now))
            else:
                changed=self.db.execute('UPDATE activity_sessions SET phase=?,version=?,public=?,private=?,updated=? WHERE id=? AND version=?',
                    (turn.phase,version,bounded_json(turn.public),bounded_json(turn.private),now,session['id'],session['version'])).rowcount
                if not changed:raise RuntimeError('Activity version conflict')
            self.db.execute('INSERT INTO activity_events(session_id,event_key,version,event,transition,created) VALUES(?,?,?,?,?,?)',
                (session['id'],event.key,version,bounded_json(event.__dict__),bounded_json(turn.__dict__),now))
            for ordinal,text in enumerate(turn.messages):self._reply(session,row,text,version,ordinal)
            self.db.execute("UPDATE activity_timers SET status='cancelled' WHERE session_id=?",(session['id'],))
            if turn.phase=='active':
                for timer in turn.timers:
                    self.db.execute('INSERT OR REPLACE INTO activity_timers VALUES(?,?,?,?,?,?)',
                        (session['id'],timer['name'],version,now+timer['delay'],bounded_json(timer.get('payload',{})),'waiting'))
            self.db.execute("UPDATE activity_inbox SET status='done',error=NULL WHERE id=?",(row['id'],))
        print(json.dumps({'event':'activity_transition','session':session['id'],'skill':session['skill'],
            'group_id':session['group_id'],'version':version,'phase':turn.phase,'messages':len(turn.messages)}),flush=True)

    def export(self,session_id):
        return [json.loads(row[0]) for row in self.db.execute('SELECT event FROM activity_events WHERE session_id=? ORDER BY version',(session_id,))]
