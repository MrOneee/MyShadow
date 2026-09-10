"""Unified subjects and versioned base facts; access remains assertion-scoped."""
import json
import re
import time

CORE=('interest','background')

def topic(slot,value):
    text=re.sub(r'\s+','',value).strip('。！!，,')
    if slot=='interest':
        text=re.sub(r'^(?:我|本人|用户)?(?:现在|目前)?(?:已经)?(?:不再|不|很)?(?:喜欢|喜爱|热爱|爱好|爱)', '',text)
    return text[:60]

def normalized(value):
    return re.sub(r'^(?:我|本人|用户)', '',re.sub(r'\s+','',value)).strip('。！!，,')

def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS subjects(member TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS profile_scopes(scope TEXT PRIMARY KEY,private_owner TEXT)')
    columns={r[1] for r in db.execute('PRAGMA table_info(facts)')}
    for name,definition in [('topic',"TEXT NOT NULL DEFAULT ''"),('visibility',"TEXT NOT NULL DEFAULT 'local'"),
                            ('asserted_at','REAL'),('conflict','INTEGER NOT NULL DEFAULT 0'),('replaced_by','INTEGER')]:
        if name not in columns:db.execute(f'ALTER TABLE facts ADD COLUMN {name} {definition}')
    db.execute('CREATE INDEX IF NOT EXISTS profile_topic ON facts(member,slot,topic,status)')
    db.execute('INSERT OR IGNORE INTO subjects(member) SELECT DISTINCT member FROM controls')
    for r in db.execute("SELECT id,slot,value,recorded_at FROM facts WHERE asserted_at IS NULL").fetchall():
        db.execute('UPDATE facts SET topic=?,asserted_at=? WHERE id=?',(topic(r['slot'],r['value']),r['recorded_at'],r['id']))

def register(db,scope,member,private):
    if not db.execute('SELECT 1 FROM subjects WHERE member=?',(member,)).fetchone():
        db.execute('INSERT OR IGNORE INTO subjects(member) VALUES(?)',(member,))
    if not db.execute('SELECT 1 FROM profile_scopes WHERE scope=?',(scope,)).fetchone():
        db.execute('INSERT OR IGNORE INTO profile_scopes VALUES(?,?)',(scope,member if private else None))

def is_private(db,scope,member):
    row=db.execute('SELECT private_owner FROM profile_scopes WHERE scope=?',(scope,)).fetchone()
    return bool(row and row[0]==member)

def access(db,scope,member,alias='f'):
    """SQL predicate applied before ranking/limits, including hidden replacement suppression."""
    private=int(is_private(db,scope,member))
    visible=f"({alias}.scope=? OR ({alias}.slot IN ('interest','background') AND ({alias}.visibility='shared' OR ?)))"
    replacement=f"({alias}.replaced_by IS NULL OR EXISTS(SELECT 1 FROM facts successor WHERE successor.id={alias}.replaced_by AND (successor.scope=? OR (successor.slot IN ('interest','background') AND (successor.visibility='shared' OR ?)))))"
    return f'{alias}.member=? AND {visible} AND {replacement}',(member,scope,private,scope,private)

def deduplicate(rows):
    seen=set();result=[]
    for row in rows:
        key=(row['slot'],row['topic'],normalized(row['value']),row['status']) if row['slot'] in CORE else ('local',row['id'])
        if key in seen:continue
        seen.add(key);result.append(row)
    return result

def write(db,scope,member,item,version,words,now=None,confidence='high'):
    now=time.time() if now is None else now
    slot,value=item['slot'],item['value'];core=slot in CORE
    key=item.get('topic') or topic(slot,value)
    if not isinstance(key,str) or not 1<=len(key)<=60:key=topic(slot,value)
    asserted=item.get('_asserted_at',now)
    same=db.execute("SELECT * FROM facts WHERE scope=? AND member=? AND slot=? AND value=? AND status='active' AND conflict=0",(scope,member,slot,value)).fetchone()
    if same and not item.get('supersedes'):return same['id']
    candidates=[];older=[];conflict=0;status='active';replaced_by=None
    if core:
        candidates=db.execute("SELECT * FROM facts WHERE member=? AND slot=? AND topic=? AND status='active'",(member,slot,key)).fetchall()
        differing=[r for r in candidates if normalized(r['value'])!=normalized(value)]
        if differing:
            latest=max(differing,key=lambda r:r['asserted_at'])
            if asserted<latest['asserted_at']:
                status='superseded';replaced_by=latest['id']
            elif item.get('replace') is True:
                older=differing
            else:
                conflict=1
                db.executemany('UPDATE facts SET conflict=1 WHERE id=?',[(r['id'],) for r in differing])
    elif item.get('replace') or slot=='nickname':
        older=db.execute("SELECT * FROM facts WHERE scope=? AND member=? AND slot=? AND status='active'",(scope,member,slot)).fetchall()
        if slot!='nickname' and len(older)>1:older=[]
    if item.get('supersedes'):
        target=db.execute('SELECT * FROM facts WHERE id=? AND member=?',(item['supersedes'],member)).fetchone()
        if target:
            status='active';replaced_by=None;conflict=0
            related=db.execute("SELECT * FROM facts WHERE member=? AND slot=? AND topic=? AND status='active'",(member,target['slot'],target['topic'])).fetchall() if core else [target]
            older=list({r['id']:r for r in [*older,*related]}.values())
    cur=db.execute('INSERT INTO facts(scope,member,slot,value,kind,confidence,recorded_at,valid_from,status,evidence,supersedes,version,topic,visibility,asserted_at,conflict,replaced_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (scope,member,slot,value,item.get('kind','self_report'),confidence,now,item.get('valid_from'),status,
         json.dumps(item.get('evidence',[]),ensure_ascii=False),item.get('supersedes') or (older[0]['id'] if older else None),version,key,
         item.get('_visibility','local') if core else 'local',asserted,conflict,replaced_by))
    ident=cur.lastrowid
    for r in older:
        db.execute("UPDATE facts SET status='superseded',valid_to=?,replaced_by=?,conflict=0 WHERE id=?",(now,ident,r['id']))
        # Every obsolete version points to the current replacement, preventing indirect ACL leaks.
        db.execute('UPDATE facts SET replaced_by=? WHERE member=? AND replaced_by=?',(ident,member,r['id']))
    db.execute('INSERT INTO fact_search VALUES(?,?)',(ident,words(value)))
    db.execute('UPDATE subjects SET revision=revision+1 WHERE member=?',(member,))
    return ident

def barrier(db,member,now):
    db.execute('UPDATE controls SET version=version+1,since=? WHERE member=?',(now,member))
    db.execute('DELETE FROM events WHERE member=? AND processed=0',(member,))
    db.execute('UPDATE subjects SET revision=revision+1 WHERE member=?',(member,))
