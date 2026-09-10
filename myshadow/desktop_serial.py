"""Reentrant thread/process lease for the single WeChat desktop."""
import fcntl
import functools
import threading
from pathlib import Path


class DesktopLease:
    def __init__(self,path):
        self.path=Path(path);self.lock=threading.RLock();self.local=threading.local()

    def acquire(self,blocking=True):
        if not self.lock.acquire(blocking=blocking):return False
        depth=getattr(self.local,'depth',0)
        if not depth:
            stream=None
            try:
                stream=self.path.open('a')
                fcntl.flock(stream,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                self.local.stream=stream
            except BlockingIOError:
                if stream:stream.close()
                self.lock.release();return False
            except BaseException:
                if stream:stream.close()
                self.lock.release();raise
        self.local.depth=depth+1
        return True

    def release(self):
        depth=getattr(self.local,'depth',0)
        if not depth:raise RuntimeError('Desktop lease is not owned')
        self.local.depth=depth-1
        if depth==1:
            fcntl.flock(self.local.stream,fcntl.LOCK_UN);self.local.stream.close()
        self.lock.release()

    def __enter__(self):self.acquire();return self
    def __exit__(self,*args):self.release()


DESKTOP=DesktopLease(Path(__file__).resolve().parents[1]/'desktop.lock')


def serialized(function):
    @functools.wraps(function)
    def call(*args,**kwargs):
        with DESKTOP:return function(*args,**kwargs)
    return call
