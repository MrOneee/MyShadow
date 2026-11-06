"""Bounded cross-batch evidence; preserve the existing scoring policy."""
import json
import re
from datetime import datetime


def relation(store,db,scope,member,rows,proposal,events,now,tz,version):
    db.execute('''CREATE TABLE IF NOT EXISTS relationship_candidates(
        scope TEXT,member TEXT,bucket INTEGER,version INTEGER,proposal TEXT,updated REAL,
        PRIMARY KEY(scope,member,bucket))''')
    db.execute('DELETE FROM relationship_candidates WHERE updated<?',(now-86400,))
    own=[r for r in rows if r['member']==member]
    if not own:return
    revision=own[0]['version']
    valid=(proposal.get('type') in ('positive','friction','repair') and
        proposal.get('confidence')=='high' and store._evidence(proposal,member,events))
    evidence=[events[e['id']] for e in proposal.get('evidence',[])] if valid else []
    if valid and proposal['type']=='friction' and len({r['text'] for r in evidence})<2:valid=False
    if valid:
        # Retain references/offsets, never another copy of private quotations.
        saved={'type':proposal['type'],'confidence':'high','evidence':[
            {'id':e['id'],'start':events[e['id']]['text'].index(e['quote']),'length':len(e['quote'])}
            for e in proposal['evidence']]}
        for bucket in {int(r['created']//1800) for r in evidence}:
            db.execute('INSERT OR REPLACE INTO relationship_candidates VALUES(?,?,?,?,?,?)',
                (scope,member,bucket,revision,json.dumps(saved),now))
    # Revisit only windows touched by this batch. Ordinary messages alone still
    # cannot score: a confirmed exchange/activity in the same window is required.
    had_exchange=False
    for bucket in sorted({int(r['created']//1800) for r in own}):
        row=db.execute("SELECT * FROM events WHERE scope=? AND member=? AND version=? "
            "AND kind IN ('exchange','activity') AND created>=? AND created<? "
            "ORDER BY length(text) DESC,id DESC LIMIT 1",
            (scope,member,revision,bucket*1800,(bucket+1)*1800)).fetchone()
        if row is None:continue
        had_exchange=True
        typ='neutral';checked={}
        candidate=db.execute('SELECT proposal FROM relationship_candidates WHERE scope=? AND member=? AND bucket=? AND version=?',
            (scope,member,bucket,revision)).fetchone()
        if candidate:
            saved=json.loads(candidate[0]);refs=saved.get('evidence',[])
            ids=[e['id'] for e in refs]
            trusted={}
            for ident in ids:
                original=db.execute('SELECT * FROM events WHERE id=? AND scope=? AND member=? AND version=?',
                    (ident,scope,member,revision)).fetchone()
                if original:trusted[ident]=original
            saved['evidence']=[{'id':e['id'],'quote':trusted[e['id']]['text'][e['start']:e['start']+e['length']]}
                for e in refs if e['id'] in trusted]
            if len(saved['evidence'])==len(refs) and store._evidence(saved,member,trusted):
                typ=saved['type'];checked=saved
        source=str(bucket);day=datetime.fromtimestamp(row['created'],tz).date().isoformat()
        existing=db.execute('SELECT * FROM relationship_events WHERE scope=? AND member=? AND source=?',(scope,member,source)).fetchone()
        if existing and (existing['affinity'] or typ=='neutral'):continue
        count=db.execute('SELECT count(*),coalesce(sum(familiarity),0),coalesce(sum(abs(affinity)),0) FROM relationship_events WHERE scope=? AND member=? AND day=?',(scope,member,day)).fetchone()
        meaningful=len(re.sub(r'[\W_]+','',row['text']))>=8 or row['kind']=='activity'
        repeated=db.execute("SELECT 1 FROM events WHERE scope=? AND member=? AND text=? AND kind IN ('exchange','activity') AND id!=? AND created<? AND created>? LIMIT 1",
            (scope,member,row['text'],row['id'],bucket*1800,row['created']-7*86400)).fetchone()
        if repeated:meaningful=False
        df=min(2,max(0,4-count[1])) if meaningful and not existing else 0
        da={'positive':1,'friction':-2,'repair':1}.get(typ,0) if meaningful else 0
        if count[2]+abs(da)>6:da=0
        if not da:typ='neutral'
        quotes=checked.get('evidence',[]) if typ!='neutral' else [{'id':row['id']}]
        if existing:
            db.execute('UPDATE relationship_events SET affinity=?,kind=?,evidence=? WHERE id=?',
                (da,typ,json.dumps(json.loads(existing['evidence'])+quotes),existing['id']))
        else:
            db.execute('INSERT INTO relationship_events(scope,member,source,day,familiarity,affinity,kind,evidence,created,reducer_version) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (scope,member,source,day,df,da,typ,json.dumps(quotes),row['created'],version))
    if had_exchange:store._rebuild(db,scope,member,now)
