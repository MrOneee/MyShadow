"""Source-checked, local event evidence, separate from long-lived profiles."""
import json
import re
from . import member_profiles as profiles

# A second guard for the new evidence path. The existing host eligibility check
# remains mandatory; this does not weaken the profile extraction policy.
SENSITIVE = re.compile(r'\b(?:transgender|sexuality|sexual orientation|gay|lesbian|bisexual|religion|religious|christian|muslim|jewish|church|mosque|synagogue|political|republican|democrat|diagnosed|diagnosis|depression|cancer|hiv|medical history|password|passcode|salary|income|bank account|credit card|social security)\b',re.I)
KINDS={'event','state','preference','plan','advice'}


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS memory_evidence(
        id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL,member TEXT NOT NULL,
        event_id INTEGER NOT NULL,role TEXT NOT NULL,kind TEXT NOT NULL,
        summary TEXT NOT NULL,quote TEXT NOT NULL,asserted_at REAL NOT NULL,received REAL NOT NULL,
        UNIQUE(event_id,role,quote))''')
    db.execute('CREATE INDEX IF NOT EXISTS memory_evidence_subject ON memory_evidence(scope,member,asserted_at)')
    db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS memory_evidence_search USING fts5(id UNINDEXED,words)')
    db.execute('''CREATE TRIGGER IF NOT EXISTS delete_memory_evidence AFTER DELETE ON memory_evidence
        BEGIN DELETE FROM memory_evidence_search WHERE id=old.id; END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS delete_event_evidence AFTER DELETE ON events
        BEGIN DELETE FROM memory_evidence WHERE event_id=old.id; END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS invalidate_event_reads AFTER DELETE ON events
        BEGIN UPDATE subjects SET revision=revision+1 WHERE member=old.member; END''')


def write(db,scope,member,proposals,events,words,eligible):
    if not isinstance(proposals,list):return
    for item in proposals[:24]:
        if not isinstance(item,dict) or type(item.get('id')) is not int:continue
        event=events.get(item['id']);role=item.get('role','user');kind=item.get('kind')
        quote=item.get('quote');summary=item.get('summary','')
        if not event or event['member']!=member or event['scope']!=scope:continue
        if kind not in KINDS or role not in ('user','assistant'):continue
        if role=='assistant' and (event['kind']!='exchange' or kind!='advice'):continue
        if role=='user' and kind=='advice':continue
        if event['kind'] not in ('message','exchange'):continue
        if not isinstance(quote,str) or not 4<=len(quote)<=700 or quote not in event['text' if role=='user' else 'assistant']:continue
        if not isinstance(summary,str) or len(summary)>160:continue
        if any(not eligible(text) or SENSITIVE.search(text) for text in (quote,summary or quote)):continue
        cur=db.execute('INSERT OR IGNORE INTO memory_evidence(scope,member,event_id,role,kind,summary,quote,asserted_at,received) VALUES(?,?,?,?,?,?,?,?,?)',
            (scope,member,event['id'],role,kind,summary,quote,event['created'],event['received']))
        if cur.rowcount:
            db.execute('INSERT INTO memory_evidence_search VALUES(?,?)',(cur.lastrowid,words(quote+' '+summary)))
            db.execute('UPDATE subjects SET revision=revision+1 WHERE member=?',(member,))


def candidates(db,scope,member,keywords,retrieval,words):
    found={}
    acl,params=profiles.access(db,scope,member,'f')
    for match,limit in (retrieval.matches(keywords) if keywords is not None else [(None,500)]):
        condition='memory_evidence_search MATCH ?' if match is not None else '1'
        order='bm25(memory_evidence_search),' if match is not None else ''
        for row in db.execute('''SELECT e.* FROM memory_evidence e JOIN memory_evidence_search s ON s.id=e.id
            WHERE '''+condition+''' AND e.scope=? AND e.member=?
            AND NOT EXISTS(SELECT 1 FROM facts f JOIN json_each(f.evidence) j
                WHERE f.scope=e.scope AND f.member=e.member AND (CASE WHEN j.type='object' THEN json_extract(j.value,'$.id') END)=e.event_id
                AND NOT ('''+acl+'''))
            ORDER BY '''+order+'''e.asserted_at DESC LIMIT ?''',(*((match,) if match is not None else ()),scope,member,*params,limit)):
            found[row['id']]=row
    return list(found.values())


def matching_sources(db,scope,member,description):
    return {r[0] for r in db.execute('SELECT event_id FROM memory_evidence WHERE scope=? AND member=? AND (instr(quote,?)>0 OR instr(summary,?)>0)',(scope,member,description,description))}
