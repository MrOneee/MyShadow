import json
import tempfile
import threading
import unittest
import urllib.request
import time
from pathlib import Path
from types import SimpleNamespace
from agent_runtime.engine import DshEngine, HarnessFailure
from activity_runtime.contracts import obj


class FakeRuntime:
    def __init__(self,script,**kwargs):
        self.script=script;self.options=kwargs;self.calls=0;self.closed=False
    def start(self):pass
    def close(self):self.closed=True
    def post(self,payload,token=None):
        spec=json.loads(Path(self.options['env']['WECHAT_HARNESS_SPEC']).read_text(encoding='utf-8'))
        req=urllib.request.Request(spec['endpoint'],json.dumps(payload).encode(),
             {'Authorization':'Bearer '+(token or spec['token']),'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=5) as result:return json.load(result)
    def run(self,text,session_id,on_notification):
        self.calls+=1
        on_notification(SimpleNamespace(method='session.event',payload={'event':{'type':'assistant/message','data':{'usage':{'totalTokens':7}}}}))
        self.script(self,self.calls)
        return SimpleNamespace(final_response='done',finish_reason='completed')


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.instances=[]
        self.ai=SimpleNamespace(config={'model':'test','api_key':'SECRET'},base_url='http://unused')
    def engine(self,script):
        def factory(**kwargs):
            runtime=FakeRuntime(script,**kwargs);self.instances.append(runtime);return runtime
        engine=DshEngine(self.temp.name,self.ai,sdk_factory=factory);engine.bind('group-a','event-1');return engine
    def test_validation_repair_stays_in_same_session_and_thread(self):
        caller=threading.get_ident();seen=[]
        def script(r,n):
            self.assertEqual(r.post({'name':'submit_result','args':{'value':{'n':'wrong'}}})['error'],'validation_failed')
            self.assertTrue(r.post({'name':'submit_result','args':{'value':{'n':2}}})['accepted'])
        def validate(value,sources):seen.append(threading.get_ident());return value
        e=self.engine(script);out=e.run([],schema=obj({'n':{'type':'integer'}}),validator=validate)
        self.assertEqual(out.value,{'n':2});self.assertEqual(seen,[caller]);self.assertTrue(self.instances[0].closed)
        self.assertEqual(out.usage['total_tokens'],7)
    def test_prose_is_not_a_result_and_correction_is_bounded(self):
        e=self.engine(lambda r,n:None)
        with self.assertRaises(HarnessFailure) as caught:e.run([],schema=obj({}))
        self.assertEqual(caught.exception.code,'result_not_submitted');self.assertEqual(self.instances[0].calls,3)
        self.assertTrue(list(Path(self.temp.name).rglob('failure.json')))
    def test_completed_cache_and_group_isolation(self):
        e=self.engine(lambda r,n:None)
        first=e.run([{'role':'user','content':'hi'}]);second=e.run([{'role':'user','content':'hi'}])
        self.assertEqual(len(self.instances),1);self.assertEqual(first.run_id,second.run_id)
        e.bind('group-b');other=e.run([{'role':'user','content':'hi'}])
        self.assertNotEqual(first.run_id,other.run_id)
        self.assertNotEqual(self.instances[0].options['dsh_home'],self.instances[1].options['dsh_home'])
    def test_unauthorized_tool_and_bad_capability(self):
        def script(r,n):
            self.assertEqual(r.post({'name':'manage_schedule','args':{}})['error'],'validation_failed')
            with self.assertRaises(urllib.error.HTTPError) as caught:r.post({},token='wrong')
            self.assertEqual(caught.exception.code,403)
        self.engine(script).run([])
    def test_effect_not_replayed_after_crash(self):
        effect=[]
        def script(r,n):
            r.post({'name':'manage_schedule','args':{'text':'x'}})
            raise RuntimeError('simulated crash')
        e=self.engine(script);tools=[{'name':'manage_schedule','parameters':obj({'text':{'type':'string'}})}]
        for _ in range(2):
            with self.assertRaises(HarnessFailure):e.run([],tools=tools,handler=lambda n,a:effect.append(a) or {'ok':True})
        self.assertEqual(effect,[{'text':'x'}])
    def test_uncertain_effect_is_not_retried(self):
        seen=[];results=[]
        def script(r,n):
            for _ in range(2):results.append(r.post({'name':'send_sticker','args':{}}))
        def handler(n,a):seen.append(1);raise OSError('lost connection after send')
        self.engine(script).run([],tools=[{'name':'send_sticker','parameters':obj({})}],handler=handler)
        self.assertEqual(len(seen),1);self.assertEqual(results[1]['error'],'delivery_uncertain')
    def test_native_evidence_reaches_validator(self):
        evidence={'event':'web_result','name':'web_search','args':{'queries':['x']},'result':{'value':{'sources':[]}}}
        def script(r,n):
            r.post(evidence);r.post({'name':'submit_result','args':{'value':{}}})
        def validate(v,s):self.assertEqual(s,[evidence]);return v
        self.engine(script).run([],schema=obj({}),validator=validate)
    def test_ptc_scope_and_mode_cache_separation(self):
        e=self.engine(lambda r,n:None)
        native=e.research([],mode='native')
        ptc=e.research([],mode='ptc')
        self.assertNotEqual(native.run_id,ptc.run_id)
        patch=json.loads(Path(self.instances[-1].options['patches'][0]).read_text())
        self.assertEqual(next(p for p in patch if p.get('id')=='tools')['config']['mode'],'ptc')
        for mode in ('native','ptc'):
            with self.assertRaisesRegex(ValueError,'read-only'):
                e.research([],mode=mode,tools=[{'name':'manage_schedule','parameters':obj({})}])
        with self.assertRaisesRegex(ValueError,'read-only'):
            e.run([],mode='ptc',tools=[{'name':'send_sticker','parameters':obj({})}])
        with self.assertRaises(ValueError):e.run([],mode='invented')
    def test_material_routing_uses_current_request_only(self):
        from agent_runtime.research import material_request
        def request(s):return material_request([{'role':'user','content':'当前提问者：甲\n当前提问：'+s}])
        self.assertTrue(request('整理一下历史记录里大家提过的书'))
        self.assertTrue(request('搜索几个来源，对比这些资料'))
        self.assertFalse(request('搜个表情包'))
        self.assertFalse(request('整理资料，定时提醒我'))
        self.assertFalse(material_request([{'role':'user','content':'整理历史记录'},{'role':'user','content':'你好'}]))
    def test_missing_group_rejected_and_secrets_not_persisted(self):
        e=self.engine(lambda r,n:None);e.group=None
        with self.assertRaises(ValueError):e.run([])
        e.bind('a');e.run([])
        for p in Path(self.temp.name).rglob('*.json'):
            self.assertNotIn('SECRET',p.read_text(encoding='utf-8'))
        self.assertTrue(all(json.loads(p.read_text(encoding='utf-8'))['closed'] for p in Path(self.temp.name).rglob('tools.json')))
    def test_runtime_admission_and_nested_inference_boundary(self):
        active=[];peaks=[];errors=[]
        def script(r,n):
            active.append(1);peaks.append(len(active));time.sleep(.03);active.pop()
        one=self.engine(script);two=self.engine(script);two.bind('another-group')
        def run(e):
            try:e.run([])
            except Exception as exc:errors.append(exc)
        workers=[threading.Thread(target=run,args=(e,)) for e in (one,two)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(timeout=10)
        self.assertFalse(errors);self.assertEqual(max(peaks),1)
        one.in_callback=True
        with self.assertRaisesRegex(RuntimeError,'Nested'):one.run([])

if __name__=='__main__':unittest.main()
