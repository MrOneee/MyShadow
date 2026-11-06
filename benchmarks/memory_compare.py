"""Compare frozen baseline and v2.1 results with a predeclared holdout."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.memory_eval import read,dump
from benchmarks.memory_eval_analyze import bootstrap


def score_files(out):return [read(p) for p in (out/'scores').rglob('*.json')]


def aggregate(rows):
    normal=[r for r in rows if r['category'] not in ('5','abstention')]
    refused=[r for r in rows if r['category'] in ('5','abstention')]
    return {'n':len(rows),'correct':sum(r['correct'] is True for r in rows),
        'accuracy':statistics.mean(r['correct'] is True for r in rows) if rows else None,
        'official_mean':statistics.mean(r['official_score'] or 0 for r in rows) if rows else None,
        'answerable_n':len(normal),'answerable_correct':sum(r['correct'] is True for r in normal),
        'answerable_official_mean':statistics.mean(r['official_score'] or 0 for r in normal) if normal else None,
        'unanswerable_n':len(refused),'unanswerable_correct':sum(r['correct'] is True for r in refused),
        'errors':sum(r['status']!='ok' for r in rows)}


def compare(baseline,current,ids,baseline_version='baseline',current_version='2.1.0'):
    rows=[];comparisons=[]
    for dataset in ('lme','locomo'):
        for version,scores in [(baseline_version,baseline),(current_version,current)]:
            for condition in ('none','oracle','memory'):
                chosen=[s for s in scores if s['dataset']==dataset and s['condition']==condition and (s['dataset'],s['qid']) in ids]
                rows.append({'dataset':dataset,'version':version,'condition':condition,**aggregate(chosen)})
        old={s['qid']:s for s in baseline if s['dataset']==dataset and s['condition']=='memory' and (dataset,s['qid']) in ids}
        new={s['qid']:s for s in current if s['dataset']==dataset and s['condition']=='memory' and (dataset,s['qid']) in ids}
        paired=[]
        for qid in sorted(old.keys() & new.keys()):
            paired.append({'qid':qid,'case':new[qid]['case'],
                'delta':int(new[qid]['correct'] is True)-int(old[qid]['correct'] is True),
                'official_delta':(new[qid]['official_score'] or 0)-(old[qid]['official_score'] or 0)})
        comparisons.append({'dataset':dataset,'paired_n':len(paired),
            'wins':[x['qid'] for x in paired if x['delta']>0],
            'losses':[x['qid'] for x in paired if x['delta']<0],
            'semantic_delta':statistics.mean(x['delta'] for x in paired) if paired else None,
            'semantic_delta_ci95':bootstrap(paired,'delta','case'),
            'official_delta':statistics.mean(x['official_delta'] for x in paired) if paired else None,
            'official_delta_ci95':bootstrap(paired,'official_delta','case')})
    return {'scores':rows,'paired':comparisons}


def main(args):
    manifest=read(args.data/'manifest.json');current=score_files(args.run)
    original=read(args.original/'scores.json');holdout=score_files(args.holdout)
    primary_ids=set(map(tuple,manifest['primary_qids']));holdout_ids=set(map(tuple,manifest['holdout_qids']))
    assert not primary_ids & holdout_ids
    expected={(s['dataset'],s['qid'],c) for s in manifest['selected'] for c in ('none','oracle','memory')}
    actual={(s['dataset'],s['qid'],s['condition']) for s in current}
    expected_holdout={(d,q,c) for d,q in holdout_ids for c in ('none','oracle','memory')}
    actual_holdout={(s['dataset'],s['qid'],s['condition']) for s in holdout}
    if expected!=actual or expected_holdout!=actual_holdout:raise ValueError('incomplete or unexpected score coverage')
    if any(s['status']!='ok' for s in current+holdout):raise ValueError('score errors require explicit review')
    result={'primary':compare(original,current,primary_ids),'holdout':compare(holdout,current,holdout_ids),
        'coverage':{'primary_questions':len(primary_ids),'holdout_questions':len(holdout_ids),
                    'current_results':len(current),'holdout_control_results':len(holdout)},
        'caveats':['Primary baseline is the earlier frozen run, not contemporaneous.',
                   'Holdout baseline is rerun with original code and identical provider settings.',
                   'LoCoMo holdout questions share the same ten conversations; not conversation-level holdout.',
                   'Automatic retrieval increases the retrieval budget; extraction is rerun, not reused.',
                   'Same-model judges and small stratified samples limit interpretation.']}
    args.destination.mkdir(parents=True,exist_ok=True)
    dump(args.destination/'comparison.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,default=Path('.remote-memory-v21/data'))
    p.add_argument('--run',type=Path,default=Path('.remote-memory-v21/run-final'))
    p.add_argument('--holdout',type=Path,default=Path('.remote-memory-v21/baseline-holdout'))
    p.add_argument('--original',type=Path,default=Path('benchmarks/memory-eval-2026-10-22'))
    p.add_argument('--destination',type=Path,default=Path('benchmarks/memory-eval-2.1-2026-10-22'))
    main(p.parse_args())
