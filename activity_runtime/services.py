"""Capabilities and role projections passed to a package, never the Bot object."""
import hashlib
import json
import time
from .contracts import clone, bounded_json


class Contents:
    def __init__(self,db):
        self.db=db
        db.execute('''CREATE TABLE IF NOT EXISTS activity_contents(
            id TEXT PRIMARY KEY,skill TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL,
            content TEXT NOT NULL,review TEXT NOT NULL,created INTEGER NOT NULL)''')
        db.commit()

    def save(self,skill,source,content,review,status='candidate'):
        if status not in ('candidate','approved','rejected'):raise ValueError('Invalid content status')
        encoded=bounded_json(content)
        ident=hashlib.sha256((skill+encoded).encode()).hexdigest()[:24]
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO activity_contents VALUES(?,?,?,?,?,?,?)',
                (ident,skill,bounded_json(source),status,encoded,bounded_json(review),int(time.time())))
        return ident

    def list(self,skill,status='approved',limit=20):
        return [dict(r)|{'content':json.loads(r['content']),'review':json.loads(r['review']),'source':json.loads(r['source'])}
                for r in self.db.execute('SELECT * FROM activity_contents WHERE skill=? AND status=? ORDER BY created DESC LIMIT ?',
                                        (skill,status,min(50,max(1,limit))))]


class Context:
    def __init__(self,skill,model,search,contents,session,event,admin_ids=(),now=None):
        self.skill,self.model,self._search,self._contents=skill,model,search,contents
        self.session,self.event=session,event
        self.now=int(time.time() if now is None else now)
        self.is_owner=event.sender==session['owner']
        self.is_admin=event.sender in admin_ids
        self.trace=session['id']+':'+event.key

    def permitted(self,action):
        roles=self.skill.manifest.get('actions',{}).get(action,[])
        return 'member' in roles or ('owner' in roles and self.is_owner) or ('admin' in roles and self.is_admin)

    @property
    def contents(self):
        if 'contents' not in self.skill.manifest['capabilities']:raise PermissionError('Content capability unavailable')
        return ScopedContents(self._contents,self.skill.manifest['id'])

    def project(self,role,public,private):
        policy=self.skill.manifest.get('roles',{}).get(role)
        if policy is None:raise PermissionError('Undeclared model role')
        return {'public':clone(public), **({'private':{k:clone(private[k]) for k in policy if k in private}} if policy else {})}

    def infer(self,role,prompt,public,private,schema,extra=None,**kwargs):
        if 'model' not in self.skill.manifest['capabilities']:raise PermissionError('Model capability unavailable')
        engine=getattr(getattr(self.model,'ai',None),'__dict__',{}).get('harness')
        if engine:engine.bind(self.session['group_id'],self.trace)
        data=self.project(role,public,private)
        data['input']=clone(extra or {})
        return self.model.call(role,prompt,data,schema,trace=self.trace,**kwargs)

    @property
    def research_available(self):
        return bool(getattr(getattr(self.model,'ai',None),'__dict__',{}).get('harness'))

    def research(self,prompt,schema,validator,request):
        if not {'model','search'}.issubset(self.skill.manifest['capabilities']):raise PermissionError('Research capabilities unavailable')
        engine=self.model.ai.harness
        return engine.research([{'role':'system','content':prompt},{'role':'user','content':request}],
            group=self.session['group_id'],key=self.trace,purpose=self.skill.manifest['id']+'.research',
            model=self.model.model,schema=schema,validator=validator,web=True,max_tokens=8000,timeout=600,max_steps=18,reasoning='low').value

    def search(self,query):
        if 'search' not in self.skill.manifest['capabilities'] or self._search is None:raise RuntimeError('Search unavailable')
        return self._search.query(query)

    def report_progress(self,text):
        if 'progress' not in self.skill.manifest['capabilities']:raise PermissionError('Progress capability unavailable')
        if hasattr(self,'_report'):self._report(text)


class ScopedContents:
    def __init__(self,contents,name):self.contents,self.name=contents,name
    def _check(self,name):
        if name!=self.name:raise PermissionError('Content namespace mismatch')
    def list(self,name,status='approved',limit=20):
        self._check(name);return self.contents.list(name,status,limit)
    def save(self,name,source,content,review,status='candidate'):
        self._check(name);return self.contents.save(name,source,content,review,status)
