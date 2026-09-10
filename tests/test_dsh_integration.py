"""Real bundled DSH against a deterministic local provider; no API key/network needed."""
import http.server
import json
import tempfile
import threading
import unittest
from types import SimpleNamespace
from agent_runtime.engine import DshEngine
from activity_runtime.contracts import obj


class DshIntegration(unittest.TestCase):
    def test_real_runtime_repairs_submission_and_loads_web_compaction(self):
        requests=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                value={'answer':'bad'} if len(requests)==1 else {'answer':42}
                call={'index':0,'id':'call_'+str(len(requests)),'type':'function',
                      'function':{'name':'submit_result','arguments':json.dumps({'value':value})}}
                chunk={'id':'test','object':'chat.completion.chunk','model':'deepseek-v4-flash',
                       'choices':[{'index':0,'delta':{'role':'assistant','tool_calls':[call]},'finish_reason':None}]}
                end={'id':'test','object':'chat.completion.chunk','model':'deepseek-v4-flash',
                     'choices':[{'index':0,'delta':{},'finish_reason':'tool_calls'}],
                     'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}}
                data=('data: '+json.dumps(chunk)+'\n\ndata: '+json.dumps(end)+'\n\ndata: [DONE]\n\n').encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as root:
            ai=SimpleNamespace(config={'model':'deepseek-v4-flash','api_key':'local-test'},base_url=f'http://127.0.0.1:{server.server_port}')
            e=DshEngine(root,ai);e.bind('test-group')
            result=e.run([{'role':'system','content':'Only submit the result.'},{'role':'user','content':'6*7'}],
                         schema=obj({'answer':{'type':'integer'}}),web=True,timeout=40)
            self.assertEqual(result.value,{'answer':42});self.assertEqual(len(requests),2)
            names={t['function']['name'] for t in requests[0]['tools']}
            self.assertTrue({'web_search','web_fetch','submit_result'}<=names)
            self.assertFalse(any('bash' in n or 'editor' in n or 'pwsh' in n for n in names))
            self.assertIn('validation_failed',json.dumps(requests[1]))

if __name__=='__main__':unittest.main()
