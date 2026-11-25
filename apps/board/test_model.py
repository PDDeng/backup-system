import datetime as dt,unittest
from model import project
class Projection(unittest.TestCase):
 def snap(self):
  return {'sampled':'2026-11-24T12:00:00+00:00','assets':[{'id':'1','targets':['a'],'copies':[{'target':'a','status':'receipt_found','date':'2026-11-24','run':'today'}]}]}
 def runmodel(self,s):return project(s,dt.datetime(2026,11,24,12,5,tzinfo=dt.timezone.utc))
 def test_missing_is_not_protected(self):
  s=self.snap();s['assets'][0]['copies']=[];self.assertEqual(self.runmodel(s)['summary']['protected'],0)
 def test_old_record_rejected(self):
  s=self.snap();s['assets'][0]['copies'][0]['date']='2026-11-19';self.assertEqual(self.runmodel(s)['summary']['attention'],1)
 def test_execution_cannot_replace_receipt(self):
  s=self.snap();s['assets'][0]['execution']={'run':'other','targets':{'a':True}};self.assertEqual(self.runmodel(s)['summary']['attention'],1)
 def test_receipt_does_not_prove_restore(self):
  r=self.runmodel(self.snap());self.assertEqual(r['summary']['protected'],1);self.assertEqual(r['summary']['restore_passed'],0)
 def test_future_date_rejected(self):
  s=self.snap();s['assets'][0]['copies'][0]['date']='2026-11-25';self.assertEqual(self.runmodel(s)['summary']['protected'],0)
 def test_stale_snapshot_explicit(self):
  s=self.snap();s['sampled']='2026-11-23T12:00:00+00:00';self.assertTrue(self.runmodel(s)['stale'])
 def test_size_checked_pending_is_not_full_verified(self):
  s=self.snap();a=s['assets'][0];a['copies'][0]['date']='2026-11-19';a['transfers']=[dict(target='a',status='files_size_rechecked',date='2026-11-24',run='today',verification='pending_md5')]
  r=self.runmodel(s);self.assertEqual(r['summary']['delivered'],1);self.assertEqual(r['summary']['protected'],0);self.assertEqual(r['assets'][0]['state'],'pending_verification')
if __name__=='__main__':unittest.main()
