"""Resource accounting on identical 160-question regression scope."""
import json
from collections import Counter
from pathlib import Path
import statistics
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.memory_eval import read,dump
from benchmarks.memory_eval_analyze import percentile


def resources(run,ids):
    cases={(d,q if d=='lme' else q.split(':')[0]) for d,q in ids}
    calls=[json.loads(x) for x in (run/'calls.jsonl').read_text(encoding='utf-8').splitlines()]
    chosen=[]
    for call in calls:
        parts=call['label'].split('/')
        if len(parts)>=3 and (tuple(parts[1:3]) in (cases if parts[0]=='ingest' else ids)):chosen.append(call)
    usage=Counter(calls=len(chosen),errors=sum('error' in x for x in chosen))
    for call in chosen:usage.update({k:call.get('usage',{}).get(k,0) for k in ('prompt_tokens','completion_tokens','total_tokens')})
    answers=[read(p) for p in (run/'answers').rglob('*_memory.json')]
    answers=[a for a in answers if (a['dataset'],a['qid']) in ids]
    latency=[]
    for dataset in ('lme','locomo'):
        a=[x for x in answers if x['dataset']==dataset]
        latency.append(dict(dataset=dataset,n=len(a),median_seconds=statistics.median(x['seconds'] for x in a),
            p95_seconds=percentile([x['seconds'] for x in a],.95),
            median_local_ms=statistics.median(x['local_read_seconds']*1000 for x in a),
            model_recall_calls=sum(len(x['tool_calls']) for x in a)))
    ingestions=[read(p) for p in (run/'stores').rglob('ingestion.json')]
    ingest=[x for x in ingestions if (x['dataset'],x['case']) in cases]
    return dict(usage=dict(usage),memory_latency=latency,store_bytes=sum(x['db_bytes'] for x in ingest),
                facts=sum(x['counters']['stored_facts'] for x in ingest),episodes=sum(x['counters']['stored_episodes'] for x in ingest),
                pending=sum(x['counters']['pending'] for x in ingest))


if __name__=='__main__':
    m=read(Path('.remote-memory-v22/data/manifest.json'));ids=set(map(tuple,m['primary_qids']))
    data={'2.1.0':resources(Path('.remote-memory-v21/run-final'),ids),
          '2.2.0':resources(Path('.remote-memory-v22/run'),ids),
          'attribution':'Same 160 questions; all shared LoCoMo ingestion assigned to regression, new 14 LME ingestions excluded.'}
    dump(Path('benchmarks/memory-eval-2.2-2026-10-22/regression-resources.json'),data)
    print(json.dumps(data,ensure_ascii=False,indent=2))
