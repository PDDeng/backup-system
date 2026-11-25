import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from distribute import Job, atomic

class RetentionTests(unittest.TestCase):
    def scenario(self, count, corrupt=False, keep=15, fast=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            c = {'asset':'test','state_dir':str(root/'state'),'key':'unused','known_hosts':'unused',
                 'host':'unused','user':'root','verify_md5':not fast,'keep_days':keep,'cleanup_mode':'delete'}
            j = Job(c)
            base = root/'target'; base.mkdir()
            t = {'id':'nas'}
            j.mount = lambda target: base
            proof = j.state/'receipts'/'nas'; proof.mkdir(parents=True)
            for n in range(count):
                # Missing calendar days must not reduce retained complete dates.
                day = (dt.date(2026,1,1)+dt.timedelta(days=n*2)).isoformat()
                folder = base/day/('run%d'%n); folder.mkdir(parents=True)
                name = 'example_gitlab_backup.tar'; data = b'backup'
                (folder/name).write_bytes(data)
                r = {'owner':'core-distributor-v1','asset':'test','run':folder.name,'date':day,
                     'files':{name:{'size':len(data),'md5':hashlib.md5(data).hexdigest()}}}
                if fast: r['verification']='pending_md5'
                atomic(folder/('pending.json' if fast else 'complete.json'),r); atomic(proof/(folder.name+'.json'),r)
            if corrupt:
                (folder/name).write_bytes(b'BROKEN-short' if fast else b'BROKEN')
                with self.assertRaises(ValueError): j.clean(t,base)
                self.assertEqual(len(list(base.glob('*/*/'+('pending.json' if fast else 'complete.json')))),count)
            else:
                self.assertEqual(j.clean(t,base),max(0,count-keep))
                self.assertEqual(len(list(base.glob('*/*/'+('pending.json' if fast else 'complete.json')))),min(count,keep))
    def test_fast_three_dates(self): self.scenario(5, keep=3, fast=True)
    def test_fast_corrupt_blocks(self): self.scenario(5, corrupt=True, keep=3, fast=True)
    def test_three_dates(self): self.scenario(5, keep=3)
    def test_less_than_three(self): self.scenario(2, keep=3)
    def test_less_than_fifteen(self): self.scenario(14)
    def test_exactly_fifteen(self): self.scenario(15)
    def test_missing_days_retains_fifteen(self): self.scenario(17)
    def test_corrupt_blocks_cleanup(self): self.scenario(17,True)

if __name__=='__main__': unittest.main()
