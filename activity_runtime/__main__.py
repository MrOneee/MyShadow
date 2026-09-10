"""Local read-only inspection: python -m activity_runtime list|audit|export."""
import argparse
import json
from pathlib import Path
import sqlite3
from .registry import Registry
from .replay import audit_session


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=['list','audit','export'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--session')
    args=parser.parse_args()
    db=sqlite3.connect((args.root/'bot-state.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        if args.operation=='list':
            result=[dict(r) for r in db.execute('SELECT id,group_id,skill,phase,version,updated FROM activity_sessions ORDER BY updated DESC LIMIT 30')]
        else:
            if not args.session:parser.error('--session is required')
            if args.operation=='export':
                result=[{'version':r['version'],'event':json.loads(r['event']),
                         'public':json.loads(r['transition'])['public'],'messages':json.loads(r['transition'])['messages']}
                        for r in db.execute('SELECT * FROM activity_events WHERE session_id=? ORDER BY version',(args.session,))]
            else:
                skill=db.execute('SELECT skill FROM activity_sessions WHERE id=?',(args.session,)).fetchone()
                if skill is None:raise ValueError('Unknown session')
                result=audit_session(db,Registry(args.root/'skills',[skill[0]]),args.session)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:db.close()


if __name__=='__main__':main()
