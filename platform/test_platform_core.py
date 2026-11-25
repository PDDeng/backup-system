import unittest
from platform_core import normalize

class EvidenceTests(unittest.TestCase):
    def asset(self):return {'id':'185','name':'GitLab185','type':'GitLab','keep':15,'copies':[{'target':'a','status':'receipt_found','age_days':0,'date':'2026-11-23','verification':'pending_md5'},{'target':'b','status':'receipt_found','age_days':0,'date':'2026-11-23','verification':'size_only'}]}
    def test_size_only_does_not_become_md5_or_restore(self):
        r=normalize(self.asset(),'now');self.assertEqual(r['coverage'],'available');self.assertEqual(r['targets'][0]['verification'],'pending_md5');self.assertEqual(r['restore']['status'],'untested')
    def test_different_dates_are_partial(self):
        a=self.asset();a['copies'][1]['date']='2026-11-22';self.assertEqual(normalize(a,'now')['coverage'],'partial')
    def test_stale_receipts_not_current(self):
        a=self.asset()
        for c in a['copies']:c['age_days']=9
        self.assertEqual(normalize(a,'now')['coverage'],'unavailable')
    def test_collection_error_is_unknown_not_missing(self):
        a=self.asset();a['copies'][1]={'target':'b','status':'error','error':'timeout'}
        self.assertEqual(normalize(a,'now')['coverage'],'unknown')
    def test_future_dated_backup_not_current(self):
        a=self.asset()
        for c in a['copies']:c['age_days']=-1
        self.assertEqual(normalize(a,'now')['coverage'],'unavailable')
    def test_pending_transfer_is_not_verified(self):
        a=self.asset();a['targets']=['a','b'];a['transfers']=[dict(target=t,status='files_size_rechecked',date='2026-11-25',run='current',verification='pending_md5') for t in a['targets']]
        r=normalize(a,'2026-11-25T02:00:00+08:00');self.assertEqual(r['coverage'],'pending_verification');self.assertEqual(r['restore']['status'],'untested')
    def test_missing_expected_target_not_covered(self):
        a=self.asset();a['targets']=['a','b','c'];self.assertNotEqual(normalize(a,'now')['coverage'],'available')
    def test_different_runs_not_complete(self):
        a=self.asset();a['copies'][0]['run']='first';a['copies'][1]['run']='second';self.assertEqual(normalize(a,'now')['coverage'],'partial')
    def test_fresh_date_recalculated(self):
        self.assertEqual(normalize(self.asset(),'2026-11-30T02:00:00+08:00')['coverage'],'unavailable')
    def test_execution_run_mismatch_not_complete(self):
        a=self.asset();a['execution']={'run':'new'}
        for c in a['copies']:c['run']='old'
        self.assertEqual(normalize(a,'now')['coverage'],'partial')
if __name__=='__main__':unittest.main()

