"""Evidence-backed member profiles and relationship state. No model-controlled scores."""
import contextlib
import hashlib
import json
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from social_memory import SENSITIVE, digest, member_key
from member_memory_policy import POLICY, READ_POLICY, command
import member_profiles as profiles

TZ=ZoneInfo('Asia/Shanghai')
SLOTS={'nickname','interest','communication','background','boundary'}
REL_VERSION=1
SHARE_INTENT=r'(?:允许|可以|同意).*(?:其他群|其他会话|跨群|共享)|(?:公开|共享|分享).*(?:其他群|其他会话|跨群|这条|这个|偏好|资料|信息)|(?:这条|这个|偏好|资料|信息).*共享|(?:把|将|请).{1,60}共享'


class MemoryService:
    def __init__(self,root,config=None):
        self.config=config or {};self.base=Path(root);self.root=self.base/'member-memory'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.file=self.root/'memory.sqlite3';self.thread=None;self.stop_event=threading.Event()
        opts=self.config.get('member_memory',{})
        self.shadow=opts.get('shadow',False)
        self.quiet=max(10,int(opts.get('quiet_seconds',60)))
        self.interval=max(60,int(opts.get('max_wait_seconds',600)))
        self.batch=max(4,min(40,int(opts.get('batch_messages',20))))
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS controls(scope TEXT,member TEXT,version INTEGER DEFAULT 0,
                    disabled INTEGER DEFAULT 0,since REAL DEFAULT 0,migrated INTEGER DEFAULT 0,PRIMARY KEY(scope,member));
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,member TEXT,
                    source TEXT,kind TEXT,text TEXT,assistant TEXT,created REAL,received REAL,version INTEGER,
                    processed INTEGER DEFAULT 0,UNIQUE(scope,member,source,kind));
                CREATE INDEX IF NOT EXISTS event_pending ON events(processed,scope,id);
                CREATE INDEX IF NOT EXISTS event_subject ON events(scope,member,created);
                CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,member TEXT,
                    slot TEXT,value TEXT,kind TEXT,confidence TEXT,recorded_at REAL,valid_from TEXT,valid_to REAL,
                    status TEXT,evidence TEXT,supersedes INTEGER,version INTEGER);
                CREATE INDEX IF NOT EXISTS fact_subject ON facts(scope,member,status,slot);
                CREATE VIRTUAL TABLE IF NOT EXISTS fact_search USING fts5(id UNINDEXED,words);
                CREATE TABLE IF NOT EXISTS episodes(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,member TEXT,
                    source TEXT,text TEXT,created REAL,evidence TEXT,UNIQUE(scope,member,source));
                CREATE VIRTUAL TABLE IF NOT EXISTS episode_search USING fts5(id UNINDEXED,words);
                CREATE TABLE IF NOT EXISTS relationship_events(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,member TEXT,
                    source TEXT,day TEXT,familiarity REAL,affinity REAL,kind TEXT,evidence TEXT,created REAL,
                    reducer_version INTEGER,UNIQUE(scope,member,source));
                CREATE TABLE IF NOT EXISTS relationships(scope TEXT,member TEXT,familiarity REAL DEFAULT 0,
                    affinity REAL DEFAULT 50,active_days INTEGER DEFAULT 0,stage INTEGER DEFAULT 0,
                    friction_until REAL DEFAULT 0,updated REAL DEFAULT 0,PRIMARY KEY(scope,member));
                CREATE TABLE IF NOT EXISTS jobs(scope TEXT PRIMARY KEY,lease_until REAL DEFAULT 0,token TEXT,error TEXT);
                CREATE TABLE IF NOT EXISTS operations(scope TEXT,member TEXT,source TEXT,result TEXT,created REAL,
                    PRIMARY KEY(scope,member,source));
            ''')
            db.execute("INSERT OR IGNORE INTO meta VALUES('started',?)",(str(time.time()),))
            profiles.migrate(db)
        self.file.chmod(0o600)

    @contextlib.contextmanager
    def db(self):
        db=sqlite3.connect(self.file,timeout=5);db.row_factory=sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000');db.execute('PRAGMA cache_size=-1024')
        try:
            with db:yield db
        finally:db.close()

    def scope(self,group):
        persona=self.config.get('group_personas',{}).get(group,{})
        ident=persona.get('persona_id') or persona.get('prompt_file') or self.config.get('system_prompt_file','default')
        return digest(self.config.get('bot_id','default')+'\0'+ident+'\0'+group)

    def member(self,sender):return digest(self.config.get('bot_id','default')+'\0'+sender)

    def _ensure(self,db,group,sender):
        scope,member=self.scope(group),self.member(sender)
        profiles.register(db,scope,member,group==sender)
        row=db.execute('SELECT * FROM controls WHERE scope=? AND member=?',(scope,member)).fetchone()
        if row and row['migrated']:return row
        if row is None:
            db.execute('INSERT OR IGNORE INTO controls(scope,member) VALUES(?,?)',(scope,member))
            row=db.execute('SELECT * FROM controls WHERE scope=? AND member=?',(scope,member)).fetchone()
        if not row['migrated']:
            # Import existing evidence as legacy; never infer historical relationship scores.
            legacy=self.base/'social-memory';old_room=digest(group);old_member=member_key(group,sender)
            migration_key='legacy_scope:'+digest(self.config.get('bot_id','default')+'\0'+group+'\0'+sender)
            db.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)',(migration_key,scope))
            migration_scope=db.execute('SELECT value FROM meta WHERE key=?',(migration_key,)).fetchone()[0]
            disabled=False
            if (legacy/'queue.sqlite3').is_file():
                with contextlib.closing(sqlite3.connect((legacy/'queue.sqlite3').as_uri()+'?mode=ro',uri=True)) as old:
                    previous=old.execute('SELECT disabled FROM members WHERE room=? AND member=?',(old_room,old_member)).fetchone()
                    disabled=bool(previous and previous[0])
            path=legacy/old_room/(old_member+'.md')
            if migration_scope==scope and not disabled and path.is_file() and not path.is_symlink() and path.stat().st_size<=16000:
                match=re.search(r'```json\n(.*?)\n```',path.read_text(encoding='utf-8'),re.S)
                data=json.loads(match[1]) if match else {}
                for item in data.get('items',[]):
                    if item.get('slot') in SLOTS and isinstance(item.get('text'),str) and not SENSITIVE.search(item['text']):
                        self._fact(db,scope,member,{'slot':item['slot'],'value':item['text'],
                            'kind':item.get('kind','self_report'),'evidence':item.get('evidence',[])},row['version'],
                            now=item.get('updated',time.time()),confidence='legacy')
            db.execute('UPDATE controls SET migrated=1,disabled=? WHERE scope=? AND member=?',(int(disabled),scope,member))
        return db.execute('SELECT * FROM controls WHERE scope=? AND member=?',(scope,member)).fetchone()

    @staticmethod
    def eligible(text):return isinstance(text,str) and bool(text.strip()) and not SENSITIVE.search(text)

    def observe(self,group,sender,source,created,text,kind='message',assistant=''):
        if not sender or not self.eligible(text) or command(text) or kind not in ('message','exchange','activity'):return
        if assistant and not self.eligible(assistant):assistant='[已确认回应；敏感正文未保存]'
        with self.db() as db:
            control=self._ensure(db,group,sender);scope,member=control['scope'],control['member']
            started=float(db.execute("SELECT value FROM meta WHERE key='started'").fetchone()[0])
            if control['disabled'] or created<started or created<=control['since']:return
            db.execute('INSERT OR IGNORE INTO events(scope,member,source,kind,text,assistant,created,received,version) VALUES(?,?,?,?,?,?,?,?,?)',
                       (scope,member,str(source),kind,text[:1000],assistant[:800],created,time.time(),control['version']))

    @staticmethod
    def _words(text):
        from background_knowledge import tokens
        return ' '.join(tokens(text))

    def _fact(self,db,scope,member,item,version,now=None,confidence='high'):
        return profiles.write(db,scope,member,item,version,self._words,now,confidence)

    def _evidence(self,item,member,events,observation=False):
        evidence=item.get('evidence',[])
        if not isinstance(evidence,list) or not 1<=len(evidence)<=4:return False
        for ev in evidence:
            if not isinstance(ev,dict) or type(ev.get('id')) is not int:return False
            row=events.get(ev['id']);quote=ev.get('quote')
            if not row or row['member']!=member or row['kind']=='activity' or not isinstance(quote,str) or not 2<=len(quote)<=200 or quote not in row['text']:return False
        return not observation or len({events[e['id']]['text'] for e in evidence})>=3

    def _relation(self,db,scope,member,rows,proposal,events,now):
        exchanges=[r for r in rows if r['member']==member and r['kind'] in ('exchange','activity')]
        if not exchanges:return
        typ='neutral'
        if proposal.get('type') in ('positive','friction','repair') and proposal.get('confidence')=='high' and self._evidence(proposal,member,events):
            typ=proposal['type']
        evidence_rows=[events[e['id']] for e in proposal.get('evidence',[]) if isinstance(e,dict) and e.get('id') in events] if typ!='neutral' else []
        if typ=='friction' and len({r['text'] for r in evidence_rows})<2:typ='neutral'
        # One half-hour interaction window, not one point per incoming message.
        buckets={int(r['created']//1800):r for r in exchanges}
        for bucket,row in sorted(buckets.items()):
            bucket_type=typ if any(int(r['created']//1800)==bucket for r in evidence_rows) else 'neutral'
            source=str(bucket);day=datetime.fromtimestamp(row['created'],TZ).date().isoformat()
            existing=db.execute('SELECT * FROM relationship_events WHERE scope=? AND member=? AND source=?',(scope,member,source)).fetchone()
            if existing and (existing['affinity'] or bucket_type=='neutral'):continue
            count=db.execute('SELECT count(*),coalesce(sum(familiarity),0),coalesce(sum(abs(affinity)),0) FROM relationship_events WHERE scope=? AND member=? AND day=?',(scope,member,day)).fetchone()
            # Very short/repeated greetings do not build a relationship on their own.
            meaningful=len(re.sub(r'[\W_]+','',row['text']))>=8 or row['kind']=='activity'
            repeated=db.execute("SELECT 1 FROM events WHERE scope=? AND member=? AND text=? AND kind IN ('exchange','activity') AND id!=? AND created<? AND created>? LIMIT 1",
                (scope,member,row['text'],row['id'],bucket*1800,row['created']-7*86400)).fetchone()
            if repeated:meaningful=False
            df=min(2,max(0,4-count[1])) if meaningful and not existing else 0
            da={'positive':1,'friction':-2,'repair':1}.get(bucket_type,0) if meaningful else 0
            if count[2]+abs(da)>6:da=0
            if not da:bucket_type='neutral'
            evidence=proposal.get('evidence',[]) if bucket_type!='neutral' else [{'id':row['id']}]
            if existing:
                db.execute('UPDATE relationship_events SET affinity=?,kind=?,evidence=? WHERE id=?',
                    (da,bucket_type,json.dumps(json.loads(existing['evidence'])+evidence),existing['id']))
            else:
                db.execute('INSERT INTO relationship_events(scope,member,source,day,familiarity,affinity,kind,evidence,created,reducer_version) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (scope,member,source,day,df,da,bucket_type,json.dumps(evidence),row['created'],REL_VERSION))
        self._rebuild(db,scope,member,now)

    def _rebuild(self,db,scope,member,now):
        rows=db.execute('SELECT * FROM relationship_events WHERE scope=? AND member=? ORDER BY created,id',(scope,member)).fetchall()
        f=0;a=50;days=set();friction=0
        for r in rows:
            f=min(100,max(0,f+r['familiarity']));a=min(100,max(0,a+r['affinity']))
            if r['familiarity']>0:days.add(r['day'])
            if r['kind']=='friction':friction=r['created']+48*3600
            elif r['kind']=='repair':friction=0
        stage=3 if f>=80 and len(days)>=20 else 2 if f>=50 and len(days)>=10 else 1 if f>=20 and len(days)>=3 else 0
        db.execute('INSERT OR REPLACE INTO relationships VALUES(?,?,?,?,?,?,?,?)',(scope,member,f,a,len(days),stage,friction,now))

    def update_once(self,ai,allowed_groups):
        allowed={self.scope(g) for g in allowed_groups};now=time.time();token=hashlib.sha256(str(time.time_ns()).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            candidates=db.execute('SELECT scope,count(*) AS n,min(received) AS oldest,max(received) AS newest FROM events WHERE processed=0 GROUP BY scope ORDER BY oldest').fetchall()
            selected=None
            for c in candidates:
                if c['scope'] not in allowed:continue
                if c['n']<self.batch and now-c['newest']<self.quiet and now-c['oldest']<self.interval:continue
                db.execute('INSERT OR IGNORE INTO jobs(scope) VALUES(?)',(c['scope'],))
                if db.execute('UPDATE jobs SET lease_until=?,token=? WHERE scope=? AND lease_until<=?',(now+180,token,c['scope'],now)).rowcount:
                    selected=c['scope'];break
            if selected is None:return False
            rows=[dict(r) for r in db.execute('SELECT * FROM events WHERE scope=? AND processed=0 ORDER BY id LIMIT 40',(selected,))]
            while (len(json.dumps(rows,ensure_ascii=False))>11000 or len({r['member'] for r in rows})>8) and len(rows)>1:rows.pop()
            members={r['member']:r['version'] for r in rows}
            previous={m:[{'slot':r['slot'],'value':r['value'],'topic':r['topic']} for r in self._visible(db,selected,m)[:12]] for m in members}
            while len(json.dumps(previous,ensure_ascii=False))>5000:
                longest=max(previous,key=lambda m:len(previous[m]))
                previous[longest].pop()
        payload={'previous':previous,'events':[{k:r[k] for k in ('id','member','kind','text','assistant','created')} for r in rows]}
        try:
            answer,_=ai.complete([{'role':'system','content':POLICY},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],max_tokens=6000)
            value=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',answer.strip()))
            if not isinstance(value,dict) or not isinstance(value.get('members'),list):raise ValueError('Invalid memory extraction')
            by_member={}
            for p in value['members']:
                if not isinstance(p,dict) or p.get('member') not in members:continue
                by_member[p['member']]=p
            events={r['id']:r for r in rows}
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                job=db.execute('SELECT token FROM jobs WHERE scope=?',(selected,)).fetchone()
                if not job or job[0]!=token:return False
                for member,version in members.items():
                    control=db.execute('SELECT version,disabled FROM controls WHERE scope=? AND member=?',(selected,member)).fetchone()
                    if not control or control['disabled'] or control['version']!=version:continue
                    proposal=by_member.get(member,{})
                    proposals=proposal.get('facts',[])
                    for fact in (proposals if isinstance(proposals,list) else [])[:6]:
                        if not isinstance(fact,dict) or fact.get('slot') not in SLOTS or fact.get('kind') not in ('self_report','observation'):continue
                        if not isinstance(fact.get('value'),str) or not 1<=len(fact['value'])<=100 or not self.eligible(fact['value']):continue
                        if fact['kind']=='observation' and fact['slot']!='communication':continue
                        if not self._evidence(fact,member,events,fact['kind']=='observation'):continue
                        date=fact.get('valid_from')
                        if date is not None:
                            try:datetime.strptime(date,'%Y-%m-%d')
                            except (ValueError,TypeError):continue
                            if not any(date in events[e['id']]['text'] for e in fact['evidence']):continue
                        # Extraction cannot grant sharing or supply host-only lineage fields.
                        item={k:fact[k] for k in ('slot','value','kind','evidence','replace','valid_from','topic') if k in fact}
                        item['_asserted_at']=max(events[e['id']]['created'] for e in fact['evidence'])
                        self._fact(db,selected,member,item,version)
                    rel=proposal.get('relationship',{})
                    self._relation(db,selected,member,rows,rel if isinstance(rel,dict) else {},events,now)
                    for r in rows:
                        if r['member']!=member or r['kind'] not in ('exchange','activity'):continue
                        text=('你们曾讨论：'+r['text'][:180]+'；已确认回应：'+r['assistant'][:180]) if r['kind']=='exchange' else r['text'][:300]
                        cur=db.execute('INSERT OR IGNORE INTO episodes(scope,member,source,text,created,evidence) VALUES(?,?,?,?,?,?)',
                                       (selected,member,r['source'],text,r['created'],json.dumps([{'id':r['id']}])) )
                        if cur.rowcount:db.execute('INSERT INTO episode_search VALUES(?,?)',(cur.lastrowid,self._words(text)))
                db.executemany('UPDATE events SET processed=1 WHERE id=?',[(r['id'],) for r in rows])
                db.execute('UPDATE jobs SET lease_until=0,error=NULL WHERE scope=? AND token=?',(selected,token))
            return True
        except Exception as exc:
            with self.db() as db:db.execute('UPDATE jobs SET lease_until=?,error=? WHERE scope=? AND token=?',(time.time()+60,type(exc).__name__,selected,token))
            raise

    def _brief(self,db,scope,member):
        row=db.execute('SELECT * FROM relationships WHERE scope=? AND member=?',(scope,member)).fetchone()
        if row is None:return {'familiarity':'初识','tone':'友好自然，保持分寸','recent_state':'平稳'}
        return {'familiarity':['初识','熟悉中','熟人','老朋友'][row['stage']],
                'tone':'相处轻松，可以自然接梗' if row['affinity']>=60 else '直接但保持礼貌和分寸' if row['affinity']<40 else '友好自然，按当前话题回应',
                'recent_state':'近期有摩擦，减少调侃，认真听对方表达；不因此降低帮助质量' if row['friction_until']>time.time() else '平稳'}

    def _visible(self,db,scope,member):
        # Explicit preferences and boundaries do not silently expire. Observations do.
        acl,params=profiles.access(db,scope,member)
        return profiles.deduplicate(db.execute("SELECT f.* FROM facts f WHERE "+acl+" AND f.status='active' AND f.conflict=0 AND (f.kind!='observation' OR f.recorded_at>?) ORDER BY CASE f.slot WHEN 'boundary' THEN 0 WHEN 'nickname' THEN 1 ELSE 2 END,f.recorded_at DESC LIMIT 100",(*params,time.time()-30*86400)).fetchall())

    def context(self,group,sender,related=(),compact=False):
        if self.shadow:return ''
        with self.db() as db:
            control=self._ensure(db,group,sender);scope,member=control['scope'],control['member']
            if control['disabled']:return json.dumps({'memory_disabled':True,'relationship':'按当前聊天自然回应，不推断长期关系'},ensure_ascii=False)
            facts=self._visible(db,scope,member)
            core=[f for f in facts if f['slot'] in ('boundary','nickname')]
            optional=[f for f in facts if f['slot'] not in ('boundary','nickname')]
            notes=core+([] if compact=='essential' else optional[:max(0,(3 if compact else 8)-len(core))])
            result={'current_speaker_key':member_key(group,sender),'relationship':self._brief(db,scope,member),
                    'speaker_notes':[{'text':f['value'],'kind':f['kind']} for f in notes],
                    'related_members':[]}
            if not compact:
                for person in related[:2]:
                    if person['sender']==sender:continue
                    peer=self._ensure(db,group,person['sender'])
                    if peer['disabled']:continue
                    result['related_members'].append({'speaker_key':member_key(group,person['sender']),'name':person['name'][:60],
                        'notes':[{'text':f['value'],'kind':f['kind']} for f in self._visible(db,scope,peer['member'])[:3]]})
        return json.dumps(result,ensure_ascii=False)

    def recall(self,group,sender,args):
        if not isinstance(args,dict) or set(args)-{'query','kind'}:return {'error':'只能查询自己的当前会话记忆。'}
        query=args.get('query','');kind=args.get('kind','all')
        if not isinstance(query,str) or len(query)>80 or kind not in ('all','profile','experience'):return {'error':'参数无效。'}
        with self.db() as db:
            control=self._ensure(db,group,sender);scope,member=control['scope'],control['member']
            if control['disabled']:return {'status':'disabled','items':[],'partial':False}
            result=[]
            for table,index in [('facts','fact_search'),('episodes','episode_search')]:
                if (table=='facts' and kind=='experience') or (table=='episodes' and kind=='profile'):continue
                col='value' if table=='facts' else 'text';date='recorded_at' if table=='facts' else 'created'
                # All filters are applied before results leave storage.
                acl,params=profiles.access(db,scope,member,'t') if table=='facts' else ('t.scope=? AND t.member=?',(scope,member))
                if query.strip():
                    terms=self._words(query).split()[:40]
                    if not terms:continue
                    match=' OR '.join('"'+t+'"' for t in terms)
                    rows=db.execute(f'SELECT t.* FROM {table} t JOIN {index} s ON s.id=t.id WHERE {index} MATCH ? AND '+acl+f' ORDER BY bm25({index}) LIMIT 8',(match,*params)).fetchall()
                else:rows=db.execute(f'SELECT t.* FROM {table} t WHERE '+acl+f' ORDER BY t.{date} DESC LIMIT 8',params).fetchall()
                if table=='facts':rows=profiles.deduplicate(rows)
                for r in rows:
                    status=r['status'] if table=='facts' else 'confirmed'
                    if table=='facts' and r['conflict']:status='needs_confirmation'
                    if table=='facts' and r['kind']=='observation' and r['recorded_at']<time.time()-30*86400:status='stale_observation'
                    result.append({'text':r[col][:300],'kind':'profile' if table=='facts' else 'experience','status':status,
                        'basis':r['kind'] if table=='facts' else 'confirmed_exchange',
                        'recorded_at':datetime.fromtimestamp(r[date],TZ).isoformat(),
                        'valid_from':r['valid_from'] if table=='facts' else None})
            return {'status':'ok' if result else 'empty','items':result[:6],'partial':len(result)>6}

    def handler(self,group,sender,request,source,readonly=False):
        cache={};reads=0;revision=None
        def handle(name,args):
            nonlocal reads,revision
            with self.db() as db:
                row=db.execute('SELECT revision FROM subjects WHERE member=?',(self.member(sender),)).fetchone()
                current=row[0] if row else 0
            if current!=revision:cache.clear();revision=current
            key=json.dumps([name,args],sort_keys=True,ensure_ascii=False)
            if key in cache:return cache[key]
            if name=='recall_memory':
                reads+=1
                result=self.recall(group,sender,args) if reads<=2 else {'error':'本轮最多回忆两次。'}
            elif name=='manage_memory' and not readonly:
                result=self.manage(group,sender,args,request,str(source)+':'+digest(key))
                if result.get('status') in ('remembered','corrected','forgotten','paused','resumed','shared','unshared'):
                    cache.clear()
            else:result={'error':'工具未开放。'}
            cache[key]=result;return result
        return handle

    def _forget_fact(self,db,scope,member,target,description):
        """Erase the version chain and dependent evidence, retaining unrelated memories."""
        core=target['slot'] in profiles.CORE
        all_facts=db.execute("SELECT * FROM facts WHERE member=? AND "+("slot IN ('interest','background')" if core else 'scope=?'),(member,) if core else (member,scope)).fetchall()
        ids={target['id']};changed=True
        if core:ids.update(r['id'] for r in all_facts if r['slot']==target['slot'] and r['topic']==target['topic'])
        while changed:
            before=len(ids)
            for r in all_facts:
                if r['id'] in ids and r['supersedes']:ids.add(r['supersedes'])
                if r['supersedes'] in ids or description in r['value']:ids.add(r['id'])
            changed=len(ids)!=before
        evidence=set()
        for r in all_facts:
            if r['id'] in ids:evidence.update(e.get('id') for e in json.loads(r['evidence']) if isinstance(e,dict) and type(e.get('id')) is int)
        evidence.update(r[0] for r in db.execute('SELECT id FROM events WHERE member=? AND instr(text,?)>0'+('' if core else ' AND scope=?'),(member,description) if core else (member,description,scope)))
        affected={scope}
        for table,index in [('episodes','episode_search'),('relationship_events',None)]:
            for r in db.execute(f'SELECT * FROM {table} WHERE member=?'+('' if core else ' AND scope=?'),(member,) if core else (member,scope)).fetchall():
                linked={e.get('id') for e in json.loads(r['evidence']) if isinstance(e,dict)}
                if linked & evidence or (table=='episodes' and description in r['text']):
                    if index:db.execute(f'DELETE FROM {index} WHERE id=?',(r['id'],))
                    db.execute(f'DELETE FROM {table} WHERE id=?',(r['id'],))
                    affected.add(r['scope'])
        db.executemany('DELETE FROM fact_search WHERE id=?',[(i,) for i in ids])
        db.executemany('DELETE FROM facts WHERE id=?',[(i,) for i in ids])
        # Unrelated facts from a shared sentence keep only evidence without the erased topic.
        for r in all_facts:
            if r['id'] not in ids:
                clean=[e for e in json.loads(r['evidence']) if e.get('id') not in evidence and description not in e.get('quote','')]
                db.execute('UPDATE facts SET evidence=? WHERE id=?',(json.dumps(clean,ensure_ascii=False),r['id']))
        db.executemany('DELETE FROM events WHERE member=? AND id=?',[(member,i) for i in evidence])
        for room in affected:self._rebuild(db,room,member,time.time())

    def _purge_legacy(self,group,sender):
        legacy=self.base/'social-memory';room=digest(group);member=member_key(group,sender)
        path=legacy/room/(member+'.md')
        if path.is_file():path.unlink()
        if (legacy/'queue.sqlite3').is_file():
            with contextlib.closing(sqlite3.connect(legacy/'queue.sqlite3',timeout=5)) as old:
                with old:
                    old.execute('DELETE FROM events WHERE room=? AND member=?',(room,member))
                    old.execute('INSERT OR REPLACE INTO members(room,member,disabled,since) VALUES(?,?,1,?)',(room,member,time.time()))

    def manage(self,group,sender,args,request,source):
        if not isinstance(args,dict) or set(args)-{'action','description','value','slot','topic','visibility'}:return {'error':'无效参数，不接受其他成员、群或文件路径。'}
        action=args.get('action');desc=args.get('description','');value=args.get('value','');slot=args.get('slot','communication')
        visibility=args.get('visibility','local');topic=args.get('topic','')
        if action not in ('view','remember','correct','forget','pause','resume','share','unshare') or slot not in SLOTS or visibility not in ('local','shared') or any(not isinstance(s,str) or len(s)>100 for s in (desc,value,topic)):return {'error':'参数无效。'}
        if visibility=='shared' and (slot not in profiles.CORE or not re.search(SHARE_INTENT,request)):
            return {'error':'只有本人明确允许，基础资料才可用于其他会话；默认保留来源范围。'}
        if action in ('remember','correct') and (not value or value not in request or not self.eligible(value)):return {'error':'只保存本人当前原话中明确、非敏感的信息，请用原话值。'}
        verbs={'remember':r'记住|记一下|以后叫|叫我|我喜欢|我不喜欢|偏好|不要叫|别叫',
               'correct':r'改|纠正|更正|其实|不是|现在|别再', 'forget':r'忘掉|忘记|删除|清空|别记|不要记|停止记录',
               'pause':r'暂停|停记|别记|不要记|停止记录','resume':r'恢复|继续记|可以记|重新记',
               'share':SHARE_INTENT,
               'unshare':r'撤回|取消共享|停止共享|不要共享|别共享|不要.*(?:其他群|跨群)'}
        if action!='view' and not re.search(verbs[action],request):return {'error':'当前消息没有明确提出这项记忆操作。'}
        if action in ('forget','pause') and re.search(r'不要忘|别忘|不能忘|不要删除|别删除',request):return {'error':'当前表达不是删除或停记请求。'}
        if (action=='share' or visibility=='shared') and re.search(r'不要|不允许|不同意|别|不能|不可以|禁止|取消|撤回',request):return {'error':'当前没有明确同意共享。'}
        if action=='forget' and not desc and command(request)!='forget' and not re.search(r'(?:全部|所有).*(?:记忆|画像|档案)|(?:记忆|画像|档案).*(?:全部|所有)',request):
            return {'error':'忘记某项时请提供description；没有明确要求不能清空全部记忆。'}
        now=time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');control=self._ensure(db,group,sender);scope,member=control['scope'],control['member']
            previous=db.execute('SELECT result FROM operations WHERE scope=? AND member=? AND source=?',(scope,member,source)).fetchone()
            if previous:return json.loads(previous[0])
            acl,params=profiles.access(db,scope,member)
            facts=profiles.deduplicate(db.execute("SELECT f.* FROM facts f WHERE "+acl+" AND f.status='active' ORDER BY f.recorded_at DESC LIMIT 100",params).fetchall())
            if action=='view':return {'status':'disabled' if control['disabled'] else 'ok','items':[{'text':r['value'],'kind':r['kind'],'status':'needs_confirmation' if r['conflict'] else 'active'} for r in facts[:20]],'partial':len(facts)>20}
            targets=[r for r in facts if desc and desc in r['value']]
            # A speaker can correct a known base assertion without exposing unseen facts to the model.
            if action=='correct' and desc and desc in request:
                targets=db.execute("SELECT * FROM facts WHERE member=? AND slot IN ('interest','background') AND status='active' AND instr(value,?)>0 ORDER BY asserted_at DESC",(member,desc)).fetchall() or targets
                grouped={}
                for r in targets:grouped.setdefault((r['slot'],r['topic']),r)
                targets=list(grouped.values())
            if action in ('correct','forget','share','unshare') and desc and len(targets)!=1:return {'error':'没有唯一匹配，请用更具体的内容说明。'}
            if action=='correct' and not desc:return {'error':'请说明要纠正的原有内容。'}
            if action in ('share','unshare') and (not desc or not targets or targets[0]['slot'] not in profiles.CORE or targets[0]['conflict']):return {'error':'只能共享已确认的基础资料，请说明具体哪一条。'}
            if action in ('remember','correct') and control['disabled']:return {'error':'当前已停记，请先明确恢复记忆。'}
            # Version barrier prevents any in-flight extraction or queued old message from resurrecting deleted facts.
            version=control['version']+1
            db.execute('UPDATE controls SET version=?,since=? WHERE scope=? AND member=?',(version,now,scope,member))
            db.execute('DELETE FROM events WHERE scope=? AND member=? AND processed=0',(scope,member))
            if (targets and targets[0]['slot'] in profiles.CORE) or action in ('share','unshare'):
                profiles.barrier(db,member,now)
                version=db.execute('SELECT version FROM controls WHERE scope=? AND member=?',(scope,member)).fetchone()[0]
            if action in ('remember','correct'):
                fact_id=self._fact(db,scope,member,{'slot':targets[0]['slot'] if targets else slot,'value':value,'kind':'self_report',
                           'supersedes':targets[0]['id'] if targets else None,'topic':topic,'replace':action=='correct','_visibility':visibility,
                           'evidence':[{'quote':value,'source':source}]},version)
            elif action in ('share','unshare'):
                if action=='share':db.execute("UPDATE facts SET visibility='shared' WHERE id=?",(targets[0]['id'],))
                else:db.execute("UPDATE facts SET visibility='local' WHERE member=? AND slot=? AND topic=?",(member,targets[0]['slot'],targets[0]['topic']))
            elif action=='forget':
                self._purge_legacy(group,sender)
                if desc:
                    self._forget_fact(db,scope,member,targets[0],desc)
                else:
                    for table,index in [('facts','fact_search'),('episodes','episode_search')]:
                        db.execute(f'DELETE FROM {index} WHERE id IN (SELECT id FROM {table} WHERE scope=? AND member=?)',(scope,member))
                    for table in ('facts','episodes','events','relationship_events','relationships','operations'):
                        db.execute(f'DELETE FROM {table} WHERE scope=? AND member=?',(scope,member))
                    db.execute('UPDATE controls SET disabled=1 WHERE scope=? AND member=?',(scope,member))
            elif action in ('pause','resume'):
                db.execute('UPDATE controls SET disabled=? WHERE scope=? AND member=?',(int(action=='pause'),scope,member))
            db.execute('UPDATE subjects SET revision=revision+1 WHERE member=?',(member,))
            result={'status':{'remember':'remembered','correct':'corrected','forget':'forgotten','pause':'paused','resume':'resumed','share':'shared','unshare':'unshared'}[action]}
            if action in ('remember','correct') and db.execute('SELECT conflict FROM facts WHERE id=?',(fact_id,)).fetchone()[0]:
                result={'status':'needs_confirmation','message':'请确认这是对该项基础资料的更正吗？'}
            # Store no request/value text in the operation receipt.
            db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(scope,member,source,json.dumps(result),now))
            return result

    def control(self,group,sender,text):
        action=command(text)
        if not action:return None
        result=self.manage(group,sender,{'action':action},text,'control:'+str(time.time_ns()))
        if 'error' in result:return result['error']
        if action=='view':return '当前已停止长期记忆。' if result['status']=='disabled' else ('目前记着：'+'；'.join(r['text']+('（待确认）' if r.get('status')=='needs_confirmation' else '') for r in result.get('items',[])) if result.get('items') else '目前还没有记下你的长期信息。')
        return '当前会话的画像、共同经历和关系记忆已清除，并停止记录。微信原始消息和短期上下文仍保留。' if action=='forget' else '从现在起恢复当前会话的记忆。'

    def stats(self):
        with self.db() as db:return {t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('events','facts','episodes','relationship_events','relationships')}

    def maintain(self):
        """Bound raw derivatives; retain compact relationship ledger for deterministic replay."""
        now=time.time()
        with self.db() as db:
            db.execute('DELETE FROM events WHERE processed=1 AND received<?',(now-90*86400,))
            db.execute('DELETE FROM operations WHERE created<?',(now-90*86400,))
            db.execute('DELETE FROM episode_search WHERE id IN (SELECT id FROM episodes WHERE created<?)',(now-180*86400,))
            db.execute('DELETE FROM episodes WHERE created<?',(now-180*86400,))
            db.execute('PRAGMA incremental_vacuum(100)')

    def start(self,ai_factory,groups):
        if self.thread:return
        def work():
            last_maintenance=0
            while not self.stop_event.wait(5):
                try:
                    self.update_once(ai_factory(),groups())
                    if time.time()-last_maintenance>3600:self.maintain();last_maintenance=time.time()
                except Exception as exc:print(json.dumps({'event':'member_memory_error','type':type(exc).__name__}),flush=True)
        self.thread=threading.Thread(target=work,name='member-memory',daemon=True);self.thread.start()
