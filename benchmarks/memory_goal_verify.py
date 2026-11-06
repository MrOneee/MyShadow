"""Export and verify a completed goal iteration before changing its source."""
import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.memory_eval import read,dump,sha,source_hashes


def main(args):
    root=args.root;dest=args.destination;m=read(root/'data/manifest.json')
    scores=[read(p) for p in (root/'run/scores').rglob('*.json')]
    answers=[read(p) for p in (root/'run/answers').rglob('*.json')]
    wanted={(s['dataset'],s['qid'],'memory') for s in m['selected']}
    checks=dict(source_hashes_match=source_hashes()==m['source_hashes'],
        harness_matches=sha('benchmarks/memory_eval.py')==m['harness_sha256'],
        datasets_match=all(sha(root/'data'/f)==v['sha256'] for f,v in m['sources'].items()))
    for name,rows in [('answers',answers),('scores',scores)]:
        checks[name+'_complete']=len(rows)==len(wanted) and {(r['dataset'],r['qid'],r['condition']) for r in rows}==wanted
        checks[name+'_error_free']=all(r['status']=='ok' for r in rows)
    calls=[json.loads(line) for line in (root/'run/calls.jsonl').read_text('utf-8').splitlines()]
    expected_cases={(s['dataset'],s['case']) for s in m['selected']}
    ingested={tuple(c['label'].split('/')[1:3]) for c in calls if c['label'].startswith('ingest/')}
    if (root/'store-copy-verification.json').exists():
        copies=read(root/'store-copy-verification.json')
        checks['original_stores_unchanged']=all(sha(args.source/'run/stores'/r['dataset']/r['case']/'member-memory/memory.sqlite3')==r['original_sha256'] for r in copies)
        copied={(r['dataset'],r['case']) for r in copies}
        checks['uncopied_cases_freshly_ingested']=copied<=expected_cases and ingested==expected_cases-copied
    else:
        checks['fresh_ingestion_for_all_cases']=m.get('ingestion_mode','').startswith('Fresh') and ingested==expected_cases
    markers=[read(p) for p in (root/'run/stores').rglob('ingestion.json')]
    checks['all_ingestion_complete']=len(markers)==len({(s['dataset'],s['case']) for s in m['selected']}) and all(r['counters']['pending']==0 for r in markers)
    checks['api_error_free']=not any('error' in c for c in calls)
    details=Counter(c['relevant_memories'].get('detail') for r in answers for c in r['provided_context'].values() if 'relevant_memories' in c)
    tool_details=Counter(c['result'].get('detail','unspecified') for r in answers for c in r.get('tool_calls',[]) if isinstance(c.get('result'),dict))
    dest.mkdir(parents=True,exist_ok=True)
    for name in ('source-snapshot.zip','tests.txt','tests-initial-failure.txt','store-copy-verification.json'):
        if (root/name).exists():shutil.copy2(root/name,dest/name)
    shutil.copy2(root/'run/calls.jsonl',dest/'calls.jsonl')
    dump(dest/'verification.json',dict(checks=checks,selection_details=dict(details),recall_tool_details=dict(tool_details)))
    print(json.dumps(dict(checks=checks,selection_details=dict(details),recall_tool_details=dict(tool_details))))
    if not all(checks.values()):raise ValueError('Verification failed')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--source',type=Path,default=Path('.remote-memory-v23'))
    main(p.parse_args())
