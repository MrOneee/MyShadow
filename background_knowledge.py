"""Local, read-only background cards. The host binds the group and directory."""
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

BACKGROUND_OUTPUT = {'type':'object','properties':{
    'status':{'type':'string'},'error':{'type':'string'},'partial':{'type':'boolean'},
    'more_matches':{'type':'boolean'},'cards':{'type':'array','items':{'type':'object',
    'properties':{k:{'type':'string'} for k in ('record_id','title','kind','period','content')},
    'required':['record_id','title','kind','period','content'],'additionalProperties':False}}},
    'additionalProperties':False}
BACKGROUND_TOOL = {'type':'function','function':{
    'name':'search_background',
    'description':'只读查询你的个人背景、熟人关系和过去经历。用人物名、别称或事件关键词查细节；这些记忆不代表任何人当前的位置或实时状态。空结果表示未收录，不表示不认识此人。',
    'parameters':{'type':'object','properties':{
        'query':{'type':'string','minLength':1,'maxLength':100,'description':'人物或经历的简短关键词，例如顾飞 摄影、潘智 书吧、顾淼 滑板'},
        'limit':{'type':'integer','minimum':1,'maximum':4}},'required':['query'],'additionalProperties':False}}}


def tokens(text):
    parts=re.findall(r'[\u4e00-\u9fff]+|[a-z0-9]+',text.lower())
    result=[]
    for part in parts:
        if re.fullmatch('[a-z0-9]+',part):result.append(part)
        elif len(part)==1:result.append(part)
        else:result.extend(part[i:i+2] for i in range(len(part)-1))
    return list(dict.fromkeys(result))


def build_index(directory):
    directory=Path(directory)
    cards=json.loads((directory/'cards.json').read_text(encoding='utf-8'))
    temp=directory/'index.next.sqlite'
    if temp.exists():temp.unlink()
    with closing(sqlite3.connect(temp)) as db:
        db.execute('CREATE TABLE cards(id TEXT PRIMARY KEY,title TEXT,kind TEXT,period TEXT,content TEXT,entities TEXT,sequence INTEGER)')
        db.execute('CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED,words,tokenize="unicode61")')
        for card in cards:
            if not 0<len(card['content'])<=8000:raise ValueError('Invalid card size')
            db.execute('INSERT INTO cards VALUES(?,?,?,?,?,?,?)',tuple(card[k] for k in ('id','title','kind','period','content'))+
                (json.dumps(card['entities'],ensure_ascii=False),card.get('sequence',0)))
            words=' '.join(tokens(' '.join([card['title'],*card['entities'],*card.get('tags',[]),card['content']])))
            db.execute('INSERT INTO search VALUES(?,?)',(card['id'],words))
        db.execute('PRAGMA user_version=1')
        db.commit()
    temp.replace(directory/'index.sqlite')
    return len(cards)


class BackgroundKnowledge:
    def __init__(self,root,config):
        self.scopes={}
        root=Path(root).resolve()
        for group,entry in config.get('group_personas',{}).items():
            if not entry.get('background'):continue
            directory=(root/entry['background']).resolve()
            if not directory.is_relative_to(root):raise ValueError('Background directory escaped root')
            index=(directory/'index.sqlite').resolve()
            if not index.is_relative_to(root) or not index.is_file():raise ValueError('Background index unavailable')
            aliases=json.loads((directory/'aliases.json').read_text(encoding='utf-8'))
            if not isinstance(aliases,dict) or not all(isinstance(k,str) and len(k)>=2 and isinstance(v,str) for k,v in aliases.items()):
                raise ValueError('Invalid background aliases')
            self.scopes[group]=(index,aliases)

    def enabled(self,group):return group in self.scopes

    def search(self,group,args,*,max_chars=2600):
        if group not in self.scopes:return {'status':'unavailable','cards':[]}
        if not isinstance(args,dict) or set(args)-{'query','limit'}:return {'error':'只能查询当前背景，不接受路径或群标识。'}
        query=args.get('query');limit=args.get('limit',4)
        if not isinstance(query,str) or not 1<=len(query.strip())<=100 or type(limit) is not int or not 1<=limit<=4:
            return {'error':'请用100字以内的关键词查询，每次最多4条。'}
        index,aliases=self.scopes[group]
        people=list(dict.fromkeys(v for k,v in aliases.items() if k in query))
        expanded=query+' '+ ' '.join(people)
        terms=tokens(expanded)[:70]
        if not terms:return {'status':'empty','cards':[],'partial':False,'more_matches':False}
        # Quoted tokens are derived by our tokenizer; no user FTS syntax or SQL is accepted.
        match=' OR '.join('"'+t+'"' for t in terms)
        try:
            with closing(sqlite3.connect(index.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                db.row_factory=sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                db.execute('PRAGMA cache_size=-512')
                rows=db.execute('SELECT c.*,bm25(search) AS rank FROM search JOIN cards c ON c.id=search.id '
                    'WHERE search MATCH ? ORDER BY rank LIMIT 100',(match,)).fetchall()
                # Common names can occur in almost every memory. Always include their
                # relationship overview even when BM25's candidate window excludes it.
                if people:
                    marks=','.join('?' for _ in people)
                    exact=db.execute("SELECT *,0.0 AS rank FROM cards WHERE kind='person' AND title IN ("+marks+')',people).fetchall()
                    rows=list({row['id']:row for row in [*rows,*exact]}.values())
            def score(row):
                entities=json.loads(row['entities'])
                hits=sum(p in entities for p in people)
                return (int(row['kind']=='person' and hits>0), hits, -row['rank'])
            rows=sorted(rows,key=score,reverse=True)
            output=[];remaining=max_chars;partial=False
            for row in rows[:limit]:
                content=row['content']
                if remaining<120:partial=True;break
                if len(content)>remaining:
                    content=content[:remaining]+'（本条仅展示部分）';partial=True
                remaining-=len(content)
                output.append({'record_id':row['id'],'title':row['title'],'kind':row['kind'],'period':row['period'],'content':content})
            return {'status':'ok' if output else 'empty','cards':output,'partial':partial,'more_matches':len(rows)>len(output)}
        except (OSError,sqlite3.Error,ValueError):
            return {'status':'failed','error':'背景记忆暂时未能读取，不能解释为没有这段经历。'}

    def automatic(self,group,text):
        if group not in self.scopes:return ''
        # At most two cards; avoid attaching identity lore to unrelated conversation.
        names=list(dict.fromkeys(v for k,v in self.scopes[group][1].items() if k in text))
        if not names:return ''
        result=self.search(group,{'query':(' '.join(names)+' '+text)[:100],'limit':2},max_chars=1000)
        people_cards=[c for c in result.get('cards',[]) if c['kind']=='person']
        if people_cards:
            result['cards']=people_cards
        return json.dumps(result,ensure_ascii=False)

    def handler(self,group):
        cache={}
        def handle(args):
            key=json.dumps(args,sort_keys=True,ensure_ascii=False)
            if key in cache:return cache[key]
            if len(cache)>=2:return {'error':'本轮已查询两次，请依据已有记忆回答；不足处明确说不确定。'}
            cache[key]=self.search(group,args)
            return cache[key]
        return handle
