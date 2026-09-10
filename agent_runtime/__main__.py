"""Read-only run diagnosis. Does not print conversation text or private game answers."""
import argparse
import json
from pathlib import Path
from .engine import digest

def inspect(root,group):
    folder=Path(root)/'groups'/digest(group)/'runs'
    rows=[]
    for run in sorted(folder.glob('*'),key=lambda p:p.stat().st_mtime,reverse=True):
        def read(name,default):
            p=run/name
            return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default
        request=read('request.json',{});status=read('status.json',{})
        rows.append({'run_id':run.name,'purpose':request.get('purpose'),**status,
                     'web_calls':len(read('web-results.json',[])),
                     'tokens':read('result.json',{}).get('usage',{}).get('total_tokens'),
                     'trace_directory':str(run.resolve())})
    return rows

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(Path(__file__).resolve().parents[1]/'harness-data'))
    parser.add_argument('--group',required=True);parser.add_argument('--limit',type=int,default=10)
    args=parser.parse_args()
    print(json.dumps(inspect(args.root,args.group)[:max(0,args.limit)],ensure_ascii=False,indent=2))
