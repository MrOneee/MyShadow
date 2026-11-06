"""Bounded lexical retrieval helpers; source quotations never cross scopes."""
import json
import math
import re
from collections import Counter

VERSION = '2.10.0'
USER_LIMIT = 1000
ASSISTANT_LIMIT = 4000
STOP = set('a an the and or of for to in on at by with from is are was were be been being do does did have has had i me my mine you your we our they their it its this that these those what which who whom when where why how many much can could would should will shall please tell remember recall before after since so far any some all about into as not'.split())
STOP.update(('请问','告诉','诉我','记得','记忆','之前','以前','什么','哪些','多少','怎么','我的','我们','有没有'))
STOP.update('s t said say mentioned mention told also get got use used want wanted like would information available'.split())
STOP.update('currently recent recently current'.split())


def terms(text, words):
    return list(dict.fromkeys(t for t in words(text).split() if t not in STOP and (len(t)>1 or t.isdigit())))[:24]


def query(text, words):
    chosen=[]
    for term in terms(text,words):
        if len(' '.join(chosen+[term]))>80:break
        chosen.append(term)
    return ' '.join(chosen)


def quotes(evidence):
    return [e['quote'] for e in json.loads(evidence) if isinstance(e,dict)
            and isinstance(e.get('quote'),str)][:4]


def index_fact(db, ident, words):
    db.execute('DELETE FROM fact_evidence_search WHERE id=?',(ident,))
    row=db.execute('SELECT evidence FROM facts WHERE id=?',(ident,)).fetchone()
    if row:db.execute('INSERT INTO fact_evidence_search VALUES(?,?)',(ident,words(' '.join(quotes(row['evidence'])))))


def episode_text(event):
    return ('用户原话：'+event['text']+'\n已确认的机器人回应：'+event['assistant']) if event['kind']=='exchange' else event['text'][:1000]


def episode_evidence(event):
    item={'id':event['id']}
    if event['kind']=='exchange':
        start=len('用户原话：');end=start+len(event['text'])
        assistant_start=end+len('\n已确认的机器人回应：')
        # Host-generated offsets, not marker parsing: a quoted marker in a
        # user's message must never turn their words into an assistant reply.
        item['parts']=[{'role':'user','start':start,'end':end},
            {'role':'assistant','start':assistant_start,'end':assistant_start+len(event['assistant'])}]
    return json.dumps([item],ensure_ascii=False)


def episode_windows(text,evidence,keywords,words):
    records=json.loads(evidence)
    parts=records[0].get('parts',[]) if records and isinstance(records[0],dict) else []
    result=[]
    for part in parts[:2]:
        start,end=part.get('start'),part.get('end');role=part.get('role')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(text) or role not in ('user','assistant'):continue
        body=text[start:end]
        if len(body)<=600:windows=[body]
        else:
            # Overlap preserves sentence context around boundaries. Ranking
            # uses only the authorized record, and returns actual substrings.
            windows=[body[i:i+600] for i in range(0,len(body),450)]
            windows=sorted(windows,key=lambda t:rank(t,keywords,words),reverse=True)[:3]
        for window in windows:
            if not keywords or rank(window,keywords,words)>0:
                result.append((window,role+'_statement',len(body)>len(window)))
    return result


def migrate(db, words):
    db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS fact_evidence_search USING fts5(id UNINDEXED,words)')
    db.execute('''CREATE TRIGGER IF NOT EXISTS delete_fact_evidence AFTER DELETE ON facts
                  BEGIN DELETE FROM fact_evidence_search WHERE id=old.id; END''')
    row=db.execute("SELECT value FROM meta WHERE key='memory_version'").fetchone()
    if row and row[0]==VERSION:return
    if row and row[0] in ('2.1.0','2.2.0','2.3.0','2.4.0','2.5.0','2.6.0','2.7.0','2.8.0','2.9.0'):
        # 2.2 changes selection and future writes, not historical content. In
        # particular do not refill source text after a user's partial deletion.
        db.execute("UPDATE meta SET value=? WHERE key='memory_version'",(VERSION,))
        return
    db.execute('DELETE FROM fact_evidence_search')
    for fact in db.execute('SELECT id,evidence FROM facts'):
        db.execute('INSERT INTO fact_evidence_search VALUES(?,?)',(fact['id'],words(' '.join(quotes(fact['evidence'])))))
    # Only upgrade episodes which survived deletion. Never recreate an episode.
    for event in db.execute('''SELECT p.id AS episode_id,e.* FROM episodes p JOIN events e
            ON p.scope=e.scope AND p.member=e.member AND p.source=e.source
            JOIN controls c ON c.scope=p.scope AND c.member=p.member
            WHERE e.kind IN ('exchange','activity') AND e.processed=1 AND c.disabled=0'''):
        text=episode_text(event)
        db.execute('UPDATE episodes SET text=? WHERE id=?',(text,event['episode_id']))
        db.execute('DELETE FROM episode_search WHERE id=?',(event['episode_id'],))
        db.execute('INSERT INTO episode_search VALUES(?,?)',(event['episode_id'],words(text)))
    db.execute("INSERT OR REPLACE INTO meta VALUES('memory_version',?)",(VERSION,))


def excerpt(text, keywords, words, limit=900):
    if len(text)<=limit:return text,False
    positions={0,max(0,len(text)-limit)}
    for term in keywords:
        for match in list(re.finditer(re.escape(term),text,re.I))[:20]:
            positions.add(max(0,min(len(text)-limit,match.start()-120)))
    def score(start):
        found=set(words(text[start:start+limit]).split())
        return sum(min(len(t),8) for t in keywords if t in found)
    start=max(sorted(positions),key=score)
    return ('…' if start else '')+text[start:start+limit]+('…' if start+limit<len(text) else ''),True


def rank(text, keywords, words):
    found=set(words(text).split())
    return sum(min(len(t),8) for t in keywords if t in found)


def matches(keywords):
    """Broad search plus bounded per-term lanes, for multi-part questions."""
    if not keywords:return []
    quoted=['"'+t.replace('"','')+'"' for t in keywords]
    return [(' OR '.join(quoted),48)]+[(t,6) for t in quoted[:12]]


def select(candidates, keywords, words, limit=12, budget=6500):
    """Choose relevant, complementary evidence without repeated source padding.

    Candidates are (text, assertion time, source keys, item, optional related terms).
    Source keys never leave the storage boundary. IDF is local to these already
    authorized candidates, so hidden records cannot affect the exposed ranking.
    """
    if not candidates:return []
    documents=[set(words(c[0]).split()) for c in candidates]
    frequencies=Counter(t for doc in documents for t in set(keywords)&doc)
    weights={t:1+math.log((len(documents)+1)/(frequencies[t]+1)) for t in keywords}
    scored=[]
    for c,doc in zip(candidates,documents):
        matched=set(keywords)&doc
        related=set(c[4])&set(keywords) if len(c)>4 else set()
        value=sum(weights[t] for t in matched)+.6*sum(weights[t] for t in related-matched)
        matched |= related
        scored.append((value,matched,c))
    selected=[];seen_sources=set();seen_text=set();covered=set();size=0
    while scored and len(selected)<limit:
        def priority(entry):
            value,matched,c=entry
            overlap=len(set(c[2])&seen_sources)/max(1,len(c[2]))
            novelty=sum(weights[t] for t in matched-covered)
            return (value*(1-.65*overlap)+.35*novelty,c[1])
        best=max(scored,key=priority);scored.remove(best)
        value,matched,c=best;item=c[3]
        text_key=(re.sub(r'\s+','',item['text']),tuple(sorted(c[2])),c[1],item.get('status'))
        if text_key in seen_text or (keywords and value<=0):continue
        length=len(json.dumps(item,ensure_ascii=False))+2
        if size+length>budget:continue
        selected.append(item);size+=length;covered.update(matched)
        seen_sources.update(c[2]);seen_text.add(text_key)
    return selected
