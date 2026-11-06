"""Compare the frozen 2.2 run to 2.1 and the fresh 34-question control."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.memory_compare import compare,score_files
from benchmarks.memory_eval import read,dump


def require_coverage(rows,ids):
    expected={(d,q,c) for d,q in ids for c in ('none','oracle','memory')}
    keys=[(s['dataset'],s['qid'],s['condition']) for s in rows]
    if set(keys)!=expected or len(keys)!=len(expected):raise ValueError('incomplete/duplicate score coverage')
    if any(s['status']!='ok' for s in rows):raise ValueError('scoring errors require explicit review')


def main(args):
    manifest=read(args.data/'manifest.json');new=score_files(args.run)
    old=read(args.baseline/'scores.json');control=score_files(args.control)
    regression=set(map(tuple,manifest['primary_qids']));holdout=set(map(tuple,manifest['holdout_qids']))
    if regression&holdout:raise ValueError('holdout overlaps regression')
    require_coverage(new,regression|holdout);require_coverage(old,regression);require_coverage(control,holdout)
    result={name:compare(old,new,set(map(tuple,manifest[key])),'2.1.0','2.2.0') for name,key in
        [('regression_160','primary_qids'),('original_126','previous_primary_qids'),('previous_34','previous_holdout_qids')]}
    result['new_holdout_34']=compare(control,new,holdout,'2.1.0','2.2.0')
    result['coverage']={'regression_questions':len(regression),'new_holdout_questions':len(holdout),
        'new_scores':len(new),'control_scores':len(control)}
    result['caveats']=['Prior 160 questions are regression, not blind holdout.',
        'Only new 34-question baseline is contemporaneously rerun.',
        'LoCoMo new questions use the same ten conversations.',
        'Same models and recall-call/character budgets; candidate/item selection budget changed.',
        'Fresh ingestion validates new writing and reading together; migrated old data can differ.',
        'Same-model judges and small stratified samples limit causal interpretation.']
    args.destination.mkdir(parents=True,exist_ok=True)
    dump(args.destination/'comparison.json',result)
    print(json.dumps(result['coverage']))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,default=Path('.remote-memory-v22/data'))
    p.add_argument('--run',type=Path,default=Path('.remote-memory-v22/run'))
    p.add_argument('--baseline',type=Path,default=Path('benchmarks/memory-eval-2.1-2026-10-22'))
    p.add_argument('--control',type=Path,default=Path('.remote-memory-v22/baseline-holdout'))
    p.add_argument('--destination',type=Path,default=Path('benchmarks/memory-eval-2.2-2026-10-22'))
    main(p.parse_args())
