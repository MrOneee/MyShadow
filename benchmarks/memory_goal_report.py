"""Summarize every frozen goal iteration; never select questions by correctness."""
import argparse
from collections import Counter
from pathlib import Path
import sys
import json
import statistics

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.memory_eval import read,dump
from benchmarks.memory_compare import aggregate,compare


def main(args):
    manifest=read(args.data/'manifest.json')
    expected={(s['dataset'],s['qid']) for s in manifest['selected']}
    scores=[read(p) for p in (args.run/'scores').rglob('*_memory.json')]
    answers=[read(p) for p in (args.run/'answers').rglob('*_memory.json')]
    for label,rows in [('scores',scores),('answers',answers)]:
        if {(r['dataset'],r['qid']) for r in rows}!=expected or len(rows)!=len(expected):raise ValueError('Incomplete '+label)
        if any(r['status']!='ok' for r in rows):raise ValueError('Errors in '+label)
    old=[r for r in read(args.baseline/'scores.json') if r['condition']=='memory' and (r['dataset'],r['qid']) in expected]
    if len(old)!=len(scores):raise ValueError('Baseline coverage differs')
    results=[]
    for dataset,threshold in [('lme',.7),('locomo',.6)]:
        selected=[s for s in scores if s['dataset']==dataset]
        stats=aggregate(selected)
        rate=stats['answerable_correct']/stats['answerable_n']
        results.append(dict(dataset=dataset,threshold=threshold,answerable_accuracy=rate,met=rate>=threshold,**stats))
    calls=[json.loads(s) for s in (args.run/'calls.jsonl').read_text(encoding='utf-8').splitlines()]
    counters=Counter()
    for c in calls:counters.update({k:c.get('usage',{}).get(k,0) for k in ('prompt_tokens','completion_tokens','total_tokens')})
    result=dict(version=manifest['memory_version'],scope='Fixed 160-question regression, memory condition only',
        metrics=results,both_targets_met=all(r['met'] for r in results),
        comparison=compare(old,scores,expected,read(args.baseline/'manifest.json').get('memory_version','baseline'),manifest['memory_version']),
        calls=len(calls),api_errors=sum('error' in c for c in calls),usage=dict(counters),
        limitation='Passing this screening alone is not final confirmation. Retest and sealed holdout required by plan.')
    args.destination.mkdir(parents=True,exist_ok=True)
    for name,obj in [('goal-metrics.json',result),('answers.json',answers),('scores.json',scores),('manifest.json',manifest)]:dump(args.destination/name,obj)
    dump(Path('.remote-memory-goal/latest-result.json'),dict(version=manifest['memory_version'],run=str(args.run),destination=str(args.destination),metrics=results,both_targets_met=result['both_targets_met']))
    print(json.dumps(dict(version=result['version'],metrics=results,both_targets_met=result['both_targets_met']),ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--baseline',type=Path,default=Path('benchmarks/memory-eval-2.2-2026-10-22'))
    main(p.parse_args())
