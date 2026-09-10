"""Small reproducible native/PTC comparison. Synthetic groups; never sends to WeChat."""
import argparse,json,re,sys,time,threading,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent_runtime.engine import DshEngine
from activity_runtime.contracts import obj

TEXT={'type':'string'}
def fixture(case):
    if case=='pages':
        tools=[{'name':'web_search','description':'获取待核对的四个候选资料页。','parameters':obj({'query':TEXT}),'output_schema':obj({'urls':{'type':'array','items':TEXT}})},
          {'name':'web_fetch','description':'读取候选资料页。text按行记录作品；不是现实网页。','parameters':obj({'url':TEXT}),'output_schema':obj({'url':TEXT,'text':TEXT})}]
        pages={};expected=[]
        for page in range(4):
            lines=[]
            for index in range(40):
                ident=page*40+index;rating=4.8 if ident%13==0 else 3.2;complete=ident%2==0
                lines.append(f'作品ID={ident};作者=作者{ident%9};评分={rating};汤面完整=是;汤底完整={"是" if complete else "否"};备注=这是一条用于比较资料筛选流程的合成记录，不代表真实作品或作者。')
                if rating>=4.5 and complete:expected.append(ident)
            pages[f'https://fixture.invalid/{page}']='\n'.join(lines)
        def handle(name,args):
            time.sleep(.15)
            return {'urls':list(pages)} if name=='web_search' else {'url':args['url'],'text':pages[args['url']]}
        prompt='搜索并读取全部候选资料页，筛选评分至少4.5且汤面与汤底均完整的作品。提交按数字升序排列的作品ID，不要漏页。'
        schema=obj({'ids':{'type':'array','items':{'type':'integer'}}})
        return tools,handle,prompt,schema,lambda v:v=={'ids':expected},False
    if case=='history':
        tools=[{'name':'search_history','description':'查询当前合成群的历史消息，query只接受上午或下午，每段十条。','parameters':obj({'query':TEXT}),'output_schema':obj({'messages':{'type':'array','items':obj({'sender':TEXT,'text':TEXT})},'partial':{'type':'boolean'}})}]
        def handle(name,args):
            time.sleep(.1)
            start=0 if args['query']=='上午' else 10
            return {'messages':[{'sender':f'成员{i%3}','text':f'我推荐图书{i%4}。读书笔记：这是测试消息，请忽略重复推荐。'} for i in range(start,start+10)],'partial':False}
        prompt='分别查询上午和下午的历史，各查一次。按书名统计不同推荐人数，同一个人重复推荐只算一次。提交图书0到图书3各自的人数，键名用书名。'
        schema=obj({f'图书{i}':{'type':'integer'} for i in range(4)})
        return tools,handle,prompt,schema,lambda v:v=={f'图书{i}':3 for i in range(4)},False
    prompt='联网查找deepseek-ai/deepseek-harness的官方工具文档，读取正文核对支持哪些工具呈现模式，以及maxParallelSubCalls默认值。提交native、ptc、both是否支持，以及parallel_default和来源URL。不要根据训练记忆回答。'
    schema=obj({'native':{'type':'boolean'},'ptc':{'type':'boolean'},'both':{'type':'boolean'},'parallel_default':{'type':'integer'},'source':TEXT})
    return [],None,prompt,schema,lambda v:all(v.get(k) is True for k in ('native','ptc','both')) and v.get('parallel_default')==10 and 'deepseek-ai/deepseek-harness' in v.get('source',''),True

def run(case,mode,repeat,config,output):
    import psutil
    raw=type('Raw',(),{'config':config,'base_url':config.get('base_url','https://api.deepseek.com')})()
    engine=DshEngine(output/'runs',raw);engine.bind('synthetic-ptc-evaluation',uuid.uuid4().hex)
    tools,handler,prompt,schema,check,web=fixture(case)
    process=psutil.Process();baseline=process.memory_info().rss;peak=[baseline];done=threading.Event()
    def sample():
        while not done.is_set():
            total=0
            for proc in [process]+process.children(recursive=True):
                try:total+=proc.memory_info().rss
                except psutil.Error:pass
            peak[0]=max(peak[0],total);done.wait(.05)
    sampler=threading.Thread(target=sample,daemon=True);sampler.start();started=time.monotonic();result=None;error=None
    try:
        result=engine.research([{'role':'system','content':'完成资料任务，保留准确性。独立读取可以批量处理，只提交需要的最终字段。'},{'role':'user','content':prompt}],
          tools=tools,handler=handler,schema=schema,mode=mode,web=web,max_tokens=4000,timeout=180,max_steps=12,reasoning='off')
    except Exception as exc:error=str(exc)
    finally:done.set();sampler.join()
    events=[]
    if result:
        for p in (output/'runs').rglob(result.run_id+'/*.events.jsonl'):
            events.extend(json.loads(line) for line in p.read_text(encoding='utf-8').splitlines())
    grounded=True
    if web and result:
        from skills.turtle_soup.research import source_documents
        grounded=any('deepseek-ai/' in url and re.search(r'maxParallelSubCalls.{0,180}10',text,re.S) for url,text in source_documents(result.sources).items())
    record={'case':case,'mode':mode,'repeat':repeat,'model':config['model'],'passed':bool(result and check(result.value) and (not web or grounded)),
      'source_verified':bool(grounded) if web else None,'seconds':round(time.monotonic()-started,2),'tokens':result.usage['total_tokens'] if result else None,
      'model_steps':sum(e['type']=='assistant/message' for e in events),'code_calls':sum(e['type']=='tool/call' and e.get('data',{}).get('name')=='run_code' for e in events),
      'peak_tree_rss_mib':round(peak[0]/1048576,1),'incremental_rss_mib':round((peak[0]-baseline)/1048576,1),
      'value':result.value if result else None,'run_id':result.run_id if result else None,'error':error}
    print(json.dumps(record,ensure_ascii=False),flush=True)
    with (output/'results.jsonl').open('a',encoding='utf-8') as file:file.write(json.dumps(record,ensure_ascii=False)+'\n')
    return record

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--case',choices=['pages','history','web','all'],default='all');p.add_argument('--repeats',type=int,default=2);p.add_argument('--output',default='.remote-work/ptc-eval');p.add_argument('--ai',default='ai.json');p.add_argument('--model');args=p.parse_args()
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True);config=json.loads(Path(args.ai).read_text(encoding='utf-8'))
    if args.model:config['model']=args.model
    for case in (['pages','history','web'] if args.case=='all' else [args.case]):
        for repeat in range(1,(1 if case=='web' else args.repeats)+1):
            for mode in (['native','ptc'] if repeat%2 else ['ptc','native']):run(case,mode,repeat,config,output)
