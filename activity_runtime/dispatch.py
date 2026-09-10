"""Bounded workers, one in-flight job per group, fair scheduling."""
from concurrent.futures import ThreadPoolExecutor
import time
import json


class GroupDispatcher:
    def __init__(self,work,max_workers=2):
        if type(max_workers) is not int or not 1<=max_workers<=4:raise ValueError('workers must be 1..4')
        self.work,self.limit=work,max_workers
        self.pool=ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix='group')
        self.running={};self.last={};self.errors={}

    def tick(self,groups,urgent=(),now=None):
        now=time.monotonic() if now is None else now
        for group,future in list(self.running.items()):
            if future.done():
                try:future.result();self.errors.pop(group,None)
                except Exception as exc:
                    self.errors[group]=type(exc).__name__+': '+str(exc)[:300]
                    print(json.dumps({'event':'group_worker_error','group_id':group,'error':self.errors[group]}),flush=True)
                self.running.pop(group);self.last[group]=now
        urgent=set(urgent)
        candidates=[g for g in groups if g not in self.running and (g in urgent or now-self.last.get(g,-100)>10)]
        candidates.sort(key=lambda g:(g not in urgent,self.last.get(g,-100)))
        for group in candidates[:max(0,self.limit-len(self.running))]:
            self.running[group]=self.pool.submit(self.work,group)
        return dict(self.errors)

    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=True)
