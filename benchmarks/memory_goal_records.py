"""Audit completed memory-goal archives; run from the repository root."""
from pathlib import Path
import json,zipfile,hashlib
read=lambda path:json.loads(path.read_text(encoding='utf-8'))
rows=[]
bases=sorted((p for p in Path('benchmarks').glob('memory-eval-2.*-20*') if int(p.name.split('-')[2].split('.')[1])>=3),key=lambda p:int(p.name.split('-')[2].split('.')[1]))
assert bases,'No archived goal iterations found'
for base in bases:
 minor=int(base.name.split('-')[2].split('.')[1])
 for phase in ('first','repeat','confirmation'):
  root=base if phase=='first' else base/phase
  path=root/('confirmation-metrics.json' if phase=='confirmation' else 'goal-metrics.json')
  if not path.exists():continue
  metrics=read(path)['metrics'];metrics={r['dataset']:r for r in metrics} if isinstance(metrics,list) else metrics
  manifest=read(root/'manifest.json');scores=read(root/'scores.json');answers=read(root/'answers.json')
  expected={(r['dataset'],r['qid'],'memory') for r in manifest['selected']}
  checks={}
  for label,items in [('answers',answers),('scores',scores)]:
   checks[label+'_complete']=len(items)==len(expected) and {(r['dataset'],r['qid'],r['condition']) for r in items}==expected
   checks[label+'_error_free']=all(r['status']=='ok' for r in items)
  verification=read(root/'verification.json');verification_checks=verification.get('checks',verification)
  checks['archived_verification_passed']=all(value is True for value in verification_checks.values())
  checks['tests_and_calls_present']=all((root/n).is_file() for n in ('tests.txt','calls.jsonl'))
  checks['report_present']=(Path('docs/memory')/f'memory-2.{minor}-evaluation.md').is_file()
  with zipfile.ZipFile(root/'source-snapshot.zip') as z:
   hashes={k:hashlib.sha256(z.read(k.replace(chr(92),'/'))).hexdigest()==v for k,v in manifest['source_hashes'].items()}
   hashes['benchmarks/memory_eval.py']=hashlib.sha256(z.read('benchmarks/memory_eval.py')).hexdigest()==manifest['harness_sha256']
   checks['snapshot_matches_manifest']=all(hashes.values())
  for dataset,metric in metrics.items():
   selected=[r for r in scores if r['dataset']==dataset];answerable=[r for r in selected if r['category'] not in ('5','abstention')]
   checks[dataset+'_metrics_match_scores']=len(answerable)==metric['answerable_n'] and sum(r['correct'] is True for r in answerable)==metric['answerable_correct'] and sum(r['correct'] is True for r in selected)==metric['correct']
  rows.append(dict(version=f'2.{minor}.0',phase=phase,directory=str(root),metrics={d:dict(correct=v['answerable_correct'],n=v['answerable_n']) for d,v in metrics.items()},checks=checks))
result=dict(scope='Completed archived goal phases only (2.3 onward); integrity and reporting consistency, not accuracy achievement or current-code compatibility',all_checks_passed=all(all(r['checks'].values()) for r in rows),phases=rows)
Path('benchmarks/memory-goal-records-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print({'phases':len(rows),'all_checks_passed':result['all_checks_passed'],'failed':[r for r in rows if not all(r['checks'].values())]})
if not result['all_checks_passed']:raise SystemExit(1)
