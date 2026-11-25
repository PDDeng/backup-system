"""Small retained fixtures; no production mount, credentials or deletion."""
import json,tempfile,unittest
from pathlib import Path
from bundle_transfer import transfer,validate_manifest

class BundleTests(unittest.TestCase):
    def fixture(self):
        root=Path(tempfile.mkdtemp(prefix='bundle-test-'));src=root/'source';dst=root/'target';src.mkdir();dst.mkdir()
        (src/'data.bkp').write_bytes(b'backup-fixture'*200)
        receipt={'schema':'core-backup-bundle/1.0','asset':'test_asset','run':'RUN1','date':'2026-11-24','files':{'data.bkp':{'size':(src/'data.bkp').stat().st_size}},'restore':'not_tested'}
        target={'id':'fixture','mount':str(dst),'source':'fixture'}
        return root,src,dst,receipt,target
    def test_traversal_rejected(self):
        _,_,_,r,_=self.fixture();r['files']={'../outside':{'size':1}}
        with self.assertRaises(ValueError):validate_manifest(r)
    def test_copy_and_idempotent_receipt(self):
        _,src,dst,r,t=self.fixture();guard=lambda *_:None
        publisher=lambda a,b:a.rename(b)
        a=transfer(src,r,t,mount_guard=guard,publisher=publisher)
        b=transfer(src,r,t,mount_guard=guard,publisher=publisher)
        final=dst/r['asset']/r['date']/r['run']
        self.assertEqual((src/'data.bkp').read_bytes(),(final/'data.bkp').read_bytes())
        self.assertTrue(b['reused']);self.assertEqual(a['status'],'success')
        self.assertEqual(json.loads((final/'complete.json').read_text())['restore'],'not_tested')
    def test_existing_unknown_bundle_preserved(self):
        _,src,dst,r,t=self.fixture();final=dst/r['asset']/r['date']/r['run'];final.mkdir(parents=True)
        (final/'complete.json').write_text('{}');(final/'keep.txt').write_text('preserve')
        with self.assertRaises(ValueError):transfer(src,r,t,mount_guard=lambda *_:None)
        self.assertEqual((final/'keep.txt').read_text(),'preserve')
    def test_failed_publication_retains_partial(self):
        _,src,dst,r,t=self.fixture()
        def fail(*_):raise OSError('publication unavailable')
        with self.assertRaises(OSError):transfer(src,r,t,mount_guard=lambda *_:None,publisher=fail)
        day=dst/r['asset']/r['date'];stages=list(day.glob('*.partial'))
        self.assertEqual(len(stages),1);self.assertTrue((stages[0]/'data.bkp').exists())
        self.assertFalse((day/r['run']).exists())

class FlatDateTests(unittest.TestCase):
    fixture=BundleTests.fixture
    def test_date_layout_and_reuse(self):
        _,src,dst,r,t=self.fixture();kwargs=dict(mount_guard=lambda *_:None,publisher=lambda a,b:a.rename(b),layout='date')
        transfer(src,r,t,**kwargs);result=transfer(src,r,t,**kwargs);final=dst/r['asset']/r['date']
        self.assertTrue((final/'data.bkp').is_file());self.assertFalse((final/r['run']).exists());self.assertTrue(result['reused'])
    def test_unknown_layout_rejected(self):
        _,src,dst,r,t=self.fixture()
        with self.assertRaises(ValueError):transfer(src,r,t,mount_guard=lambda *_:None,layout='../elsewhere')
if __name__=='__main__':unittest.main()

