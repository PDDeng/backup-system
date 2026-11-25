import datetime as dt,copy,unittest
from content_audit import verified_at
class Audit(unittest.TestCase):
 def setUp(self):
  self.now=dt.datetime.now(dt.timezone.utc);self.r=dict(status='passed',asset='asset',run='run',finished=self.now.isoformat(),results=[dict(target='t',status='verified',run='run',files=1,md5={'a':'a'*32})])
 def call(self):return verified_at(self.r,'asset','run','t',{'a':{}},self.now)
 def test_valid(self):self.assertIsNotNone(self.call())
 def test_wrong_run(self):self.r['run']='old';self.assertIsNone(self.call())
 def test_other_asset(self):self.r['asset']='other';self.assertIsNone(self.call())
 def test_failed(self):self.r['status']='failed';self.assertIsNone(self.call())
 def test_missing_file(self):self.r['results'][0]['md5']={};self.assertIsNone(self.call())
 def test_bad_hash(self):self.r['results'][0]['md5']['a']='x';self.assertIsNone(self.call())
 def test_duplicate_target(self):self.r['results']*=2;self.assertIsNone(self.call())
 def test_stale(self):self.r['finished']=(self.now-dt.timedelta(days=2)).isoformat();self.assertIsNone(self.call())
 def test_future(self):self.r['finished']=(self.now+dt.timedelta(hours=1)).isoformat();self.assertIsNone(self.call())
if __name__=='__main__':unittest.main()

class Projection(unittest.TestCase):
 def test_md5_and_restore_stay_separate(self):
  from model import project
  now=dt.datetime.now(dt.timezone.utc);day=now.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
  s={'sampled':now.isoformat(),'assets':[{'id':'185','targets':['nas243'],'execution':{'run':'new'},'transfers':[{'target':'nas243','status':'files_md5_rechecked','run':'new','date':day,'verification':'md5_verified_at '+now.isoformat()}]}]}
  a=project(s,now)['assets'][0]
  self.assertEqual(a['state'],'protected');self.assertFalse(a['copies'][0]['pending_md5']);self.assertEqual(a['restore']['status'],'not_tested')
 def test_size_only_remains_pending(self):
  from model import project
  now=dt.datetime.now(dt.timezone.utc);day=now.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
  s={'sampled':now.isoformat(),'assets':[{'id':'185','targets':['nas243'],'transfers':[{'target':'nas243','status':'files_size_rechecked','run':'new','date':day}]}]}
  self.assertEqual(project(s,now)['assets'][0]['state'],'pending_verification')
