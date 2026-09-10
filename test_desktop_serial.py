import tempfile
from pathlib import Path
import threading
import unittest
from desktop_serial import DesktopLease


class DesktopLeaseTests(unittest.TestCase):
    def test_reentrant_owner_blocks_other_threads_until_outer_release(self):
        with tempfile.TemporaryDirectory() as directory:
            lease=DesktopLease(Path(directory)/'lock')
            with lease:
                with lease:
                    outcomes=[]
                    t=threading.Thread(target=lambda:outcomes.append(lease.acquire(False)))
                    t.start();t.join(1)
                    self.assertEqual(outcomes,[False])
                self.assertTrue(lease.acquire(False));lease.release()
            self.assertTrue(lease.acquire(False));lease.release()

    def test_exception_releases_the_desktop(self):
        with tempfile.TemporaryDirectory() as directory:
            lease=DesktopLease(Path(directory)/'lock')
            with self.assertRaises(RuntimeError):
                with lease:raise RuntimeError('cancelled')
            result=[]
            def acquire():
                result.append(lease.acquire(False))
                if result[-1]:lease.release()
            t=threading.Thread(target=acquire);t.start();t.join(1)
            self.assertEqual(result,[True])
