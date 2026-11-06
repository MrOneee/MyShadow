"""Analyze completed frozen memory runs without calling a model."""
from collections import Counter, defaultdict
import argparse
import json
from pathlib import Path
import random
import sqlite3
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.memory_eval import read, dump, SEED


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)] if ordered else None


def bootstrap(rows, key, cluster):
    """Percentile cluster bootstrap; descriptive uncertainty, not population proof."""
    rng = random.Random(SEED)
    groups = defaultdict(list)
    for row in rows:
        groups[row[cluster]].append(row[key])
    keys = sorted(groups)
    if not keys:
        return None
    estimates = []
    for _ in range(4000):
        sample = [v for k in rng.choices(keys, k=len(keys)) for v in groups[k]]
        estimates.append(statistics.mean(sample))
    return [percentile(estimates, .025), percentile(estimates, .975)]


def provenance(out, result):
    """Reconstruct evidence sources from returned text; not official retrieval recall.

    Duplicate values may map to multiple rows, so this is an optimistic diagnostic.
    No reconstructed IDs were given to the answering model.
    """
    database = out / 'stores' / result['dataset'] / result['case'] / 'member-memory' / 'memory.sqlite3'
    conn = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    events = {r['id']: r['source'] for r in conn.execute('SELECT id,source FROM events')}
    facts = list(conn.execute('SELECT value,evidence FROM facts'))
    episodes = list(conn.execute('SELECT text,evidence FROM episodes'))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='memory_evidence'").fetchone():
        episodes += [{'text':r['quote'],'evidence':json.dumps([{'id':r['event_id']}])}
                     for r in conn.execute('SELECT quote,event_id FROM memory_evidence')]
    gold = set(result['evidence_ids'])
    supplied = set()
    texts = []
    for context in result['provided_context'].values():
        texts.extend(item['text'] for item in context.get('speaker_notes', []))
        texts.extend(item['text'] for item in context.get('relevant_memories',{}).get('items',[]))
    for call in result['tool_calls']:
        texts.extend(item['text'] for item in call['result'].get('items', []))
    def source_set(rows):
        answer = set()
        for row in rows:
            for ev in json.loads(row['evidence']):
                if ev.get('id') in events:
                    source = events[ev['id']]
                    answer.add(source.rsplit(':', 1)[0] if result['dataset'] == 'lme' else source)
        return answer
    for text in texts:
        supplied.update(source_set([r for r in facts if r['value'] == text]))
        supplied.update(source_set([r for r in episodes if r['text'][:300] == text or text.strip('…') in r['text']]))
    stored = source_set(facts + episodes)
    conn.close()
    return {'gold_evidence_count': len(gold), 'returned_source_count': len(supplied),
            'stored_gold_coverage': len(gold & stored) / len(gold) if gold else None,
            'returned_gold_coverage': len(gold & supplied) / len(gold) if gold else None,
            'returned_any_gold': bool(gold & supplied) if gold else None,
            'returned_all_gold': gold <= supplied if gold else None,
            'stored_all_gold': gold <= stored if gold else None}


def analyze(data, out, destination):
    manifest = read(data / 'manifest.json')
    answers = [read(p) for p in (out / 'answers').rglob('*.json')]
    scores = [read(p) for p in (out / 'scores').rglob('*.json')]
    score_map = {(s['dataset'], s['qid'], s['condition']): s for s in scores}
    conditions=read(out/'run-config.json').get('conditions',['none','oracle','memory'])
    expected = {(s['dataset'], s['qid'], c) for s in manifest['selected'] for c in conditions}
    missing = sorted(expected - set(score_map))
    diagnostics = []
    for answer in answers:
        if answer['condition'] == 'memory':
            diagnostics.append({**{k: answer[k] for k in ('dataset', 'case', 'qid', 'category')},
                                **provenance(out, answer)})
    comparisons = []
    for dataset in ('lme', 'locomo'):
        paired = []
        for s in manifest['selected']:
            if s['dataset'] != dataset:
                continue
            key = (dataset, s['qid'])
            if not all((*key, c) in score_map for c in ('none', 'oracle', 'memory')):
                continue
            mode = {c: score_map[(*key, c)] for c in ('none', 'oracle', 'memory')}
            paired.append({'qid': s['qid'], 'case': s['case'],
                           'semantic_delta': float(mode['memory']['correct'] is True) - float(mode['none']['correct'] is True),
                           'official_delta': (mode['memory']['official_score'] or 0) - (mode['none']['official_score'] or 0),
                           'oracle_gap': float(mode['oracle']['correct'] is True) - float(mode['memory']['correct'] is True)})
        comparisons.append({'dataset': dataset, 'n': len(paired),
            'semantic_delta': statistics.mean(x['semantic_delta'] for x in paired) if paired else None,
            'semantic_delta_ci95': bootstrap(paired, 'semantic_delta', 'case'),
            'official_delta': statistics.mean(x['official_delta'] for x in paired) if paired else None,
            'official_delta_ci95': bootstrap(paired, 'official_delta', 'case'),
            'oracle_gap': statistics.mean(x['oracle_gap'] for x in paired) if paired else None,
            'memory_wins': sum(x['semantic_delta'] > 0 for x in paired),
            'memory_losses': sum(x['semantic_delta'] < 0 for x in paired)})
    latency = []
    for dataset in ('lme', 'locomo'):
        for condition in ('none', 'oracle', 'memory'):
            rows = [a for a in answers if a['dataset'] == dataset and a['condition'] == condition]
            latency.append({'dataset': dataset, 'condition': condition, 'n': len(rows),
                'answer_seconds_median': percentile([a['seconds'] for a in rows], .5),
                'answer_seconds_p95': percentile([a['seconds'] for a in rows], .95),
                'local_read_ms_median': percentile([a['local_read_seconds'] * 1000 for a in rows], .5),
                'local_read_ms_p95': percentile([a['local_read_seconds'] * 1000 for a in rows], .95),
                'recall_calls': sum(len(a['tool_calls']) for a in rows),
                'recall_errors': sum('error' in c['result'] for a in rows for c in a['tool_calls'])})
    ingestions = [read(p) for p in (out / 'stores').rglob('ingestion.json')]
    ingestion_stats = []
    for dataset in ('lme', 'locomo'):
        rows = [r for r in ingestions if r['dataset'] == dataset]
        totals = Counter()
        for row in rows:
            totals.update(row['counters'])
        ingestion_stats.append({'dataset': dataset, 'cases': len(rows), 'totals': dict(totals),
            'db_bytes': sum(r['db_bytes'] for r in rows), 'summed_worker_seconds': sum(r['seconds'] for r in rows)})
    findings = []
    for dataset in ('lme', 'locomo'):
        eligible = [d for d in diagnostics if d['dataset'] == dataset and d['gold_evidence_count']]
        findings.append({'dataset': dataset, 'n_with_evidence': len(eligible),
                         'stored_all_gold': sum(d['stored_all_gold'] for d in eligible),
                         'returned_any_gold': sum(d['returned_any_gold'] for d in eligible),
                         'returned_all_gold': sum(d['returned_all_gold'] for d in eligible)})
    answerable = []
    for dataset in ('lme', 'locomo'):
        for condition in ('none', 'oracle', 'memory'):
            rows = [s for s in scores if s['dataset'] == dataset and s['condition'] == condition]
            normal = [s for s in rows if s['category'] not in ('5', 'abstention')]
            abstention = [s for s in rows if s['category'] in ('5', 'abstention')]
            answerable.append({'dataset': dataset, 'condition': condition,
                'answerable_n': len(normal), 'answerable_correct': sum(s['correct'] is True for s in normal),
                'answerable_semantic_accuracy': statistics.mean(s['correct'] is True for s in normal) if normal else None,
                'answerable_official_mean': statistics.mean(s['official_score'] or 0 for s in normal) if normal else None,
                'unanswerable_n': len(abstention), 'unanswerable_correct': sum(s['correct'] is True for s in abstention),
                'answerable_refusals': sum(a['dataset'] == dataset and a['condition'] == condition
                    and a['category'] not in ('5', 'abstention')
                    and a['prediction'].strip().lower().rstrip('.') == 'no information available' for a in answers)})
    result = {'expected_results': len(expected), 'actual_answers': len(answers), 'actual_scores': len(scores),
              'missing': missing, 'comparisons': comparisons, 'latency': latency,
              'ingestion': ingestion_stats, 'answerability': answerable, 'provenance_diagnostics': findings,
              'source_reconstruction_caveat': 'Optimistic text-to-source reconstruction, not official Recall@K',
              'scoring_errors': [s for s in scores if s['status'] != 'ok']}
    destination.mkdir(parents=True, exist_ok=True)
    dump(destination / 'analysis.json', result)
    dump(destination / 'diagnostics.json', diagnostics)
    dump(destination / 'scores.json', scores)
    dump(destination / 'answers.json', answers)
    dump(destination / 'manifest.json', manifest)
    dump(destination / 'summary.json', read(out / 'summary.json'))
    dump(destination / 'run-config.json', read(out / 'run-config.json'))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, default=Path('.remote-memory-eval'))
    p.add_argument('--output', type=Path, default=Path('.remote-memory-eval/run'))
    p.add_argument('--destination', type=Path, default=Path('benchmarks/memory-eval-2026-10-22'))
    a = p.parse_args()
    analyze(a.data.resolve(), a.output.resolve(), a.destination.resolve())
