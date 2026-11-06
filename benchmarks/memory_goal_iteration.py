"""Freeze a read-only iteration using SQLite backups, never copying answers."""
import argparse
from datetime import datetime
from pathlib import Path
import shutil
import sqlite3
import zipfile
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.memory_eval import read,dump,sha,source_hashes,TZ
from myshadow.member_memory import MemoryService
from myshadow.member_retrieval import VERSION


def main(args):
    if args.destination.exists():raise ValueError('Destination already exists')
    manifest=read(args.source/'data/manifest.json')
    manifest.update(memory_version=VERSION,source_hashes=source_hashes(),
        harness_sha256=sha('benchmarks/memory_eval.py'),created_at=datetime.now(TZ).isoformat(),
        conditions=['memory'],ingestion_mode=f'SQLite copies of {args.source}; reading-only iteration')
    data=args.destination/'data';data.mkdir(parents=True)
    for name in manifest['sources']:shutil.copy2(args.source/'data'/name,data/name)
    dump(data/'manifest.json',manifest)
    checks=[]
    for dataset,case in sorted({(s['dataset'],s['case']) for s in manifest['selected']}):
        old=args.source/'run/stores'/dataset/case
        new=args.destination/'run/stores'/dataset/case
        new.joinpath('member-memory').mkdir(parents=True)
        original=old/'member-memory/memory.sqlite3';before=sha(original)
        src=sqlite3.connect(original.resolve().as_uri()+'?mode=ro',uri=True)
        dst=sqlite3.connect(new/'member-memory/memory.sqlite3')
        try:src.backup(dst)
        finally:src.close();dst.close()
        shutil.copy2(old/'ingestion.json',new/'ingestion.json')
        store=MemoryService(new,{'bot_id':'offline-benchmark','member_memory':{'enabled':True}})
        with store.db() as db:version=db.execute("SELECT value FROM meta WHERE key='memory_version'").fetchone()[0]
        checks.append(dict(dataset=dataset,case=case,original_sha256=before,
            original_unchanged=sha(original)==before,migrated_version=version))
    if not all(r['original_unchanged'] and r['migrated_version']==VERSION for r in checks):raise ValueError('Copy verification failed')
    dump(args.destination/'store-copy-verification.json',checks)
    with zipfile.ZipFile(args.destination/'source-snapshot.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for name in [*manifest['source_hashes'],'benchmarks/memory_eval.py','benchmarks/memory_eval_analyze.py',str(args.plan)]:archive.write(name,name)
    print({'version':VERSION,'questions':len(manifest['selected']),'copied_stores':len(checks),'source_unchanged':True})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--plan',type=Path,required=True)
    main(parser.parse_args())
