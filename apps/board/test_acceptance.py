import datetime as dt
import unittest
from acceptance import validate

class AcceptanceTests(unittest.TestCase):
 def setUp(self):
  self.assets=[{'id':'gitlab185','copies':[{'run':'run-1','arrived':True}]}]
  self.record=dict(asset_id='gitlab185',backup_run='run-1',status='passed',
   tested_at=dt.datetime.now(dt.timezone.utc).isoformat(),scope='isolated login and clone',
   steps='login, clone, compare commit',actual='expected commit found',
   evidence='/isolated/evidence/log.txt',untested='CI pipelines')
 def test_valid(self):
  self.assertEqual(validate(self.record,self.assets)['backup_run'],'run-1')
 def test_missing_evidence(self):
  self.record['evidence']=' '
  with self.assertRaises(ValueError):validate(self.record,self.assets)
 def test_other_run(self):
  self.record['backup_run']='old-run'
  with self.assertRaises(ValueError):validate(self.record,self.assets)
 def test_other_asset(self):
  self.record['asset_id']='oracle41'
  with self.assertRaises(ValueError):validate(self.record,self.assets)
 def test_no_arrival(self):
  self.assets[0]['copies'][0]['arrived']=False
  with self.assertRaises(ValueError):validate(self.record,self.assets)
 def test_future(self):
  self.record['tested_at']=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat()
  with self.assertRaises(ValueError):validate(self.record,self.assets)
 def test_naive(self):
  self.record['tested_at']='2026-11-25T10:00:00'
  with self.assertRaises(ValueError):validate(self.record,self.assets)
 def test_cannot_claim_hash(self):
  self.record['status']='md5_passed'
  with self.assertRaises(ValueError):validate(self.record,self.assets)
 def test_oversize(self):
  self.record['steps']='x'*3001
  with self.assertRaises(ValueError):validate(self.record,self.assets)

if __name__=='__main__':unittest.main()
