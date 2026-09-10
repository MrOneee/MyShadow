"""Local operator inspection; never registers a chat tool or sends messages."""
import argparse
import json
from pathlib import Path
from myshadow.member_memory import MemoryService

def main():
    parser=argparse.ArgumentParser(description='Inspect scoped memory and relationship evidence locally')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--group');parser.add_argument('--member');parser.add_argument('--evidence',action='store_true')
    args=parser.parse_args()
    if bool(args.group)!=bool(args.member):parser.error('--group and --member must be supplied together')
    store=MemoryService(args.root,json.loads((args.root/'bot.json').read_text(encoding='utf-8')))
    result={'counts':store.stats()}
    with store.db() as db:
        result['pending']=db.execute('SELECT count(*) FROM events WHERE processed=0').fetchone()[0]
        result['job_errors']=[dict(r) for r in db.execute('SELECT scope,error,lease_until FROM jobs WHERE error IS NOT NULL')]
        if args.group:
            scope,member=store.scope(args.group),store.member(args.member)
            result['relationship']=dict(db.execute('SELECT * FROM relationships WHERE scope=? AND member=?',(scope,member)).fetchone() or {})
            result['profile']=store.recall(args.group,args.member,{'query':'','kind':'profile'})
            if args.evidence:
                result['relationship_evidence']=[dict(r) for r in db.execute('SELECT * FROM relationship_events WHERE scope=? AND member=? ORDER BY created DESC LIMIT 20',(scope,member))]
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
