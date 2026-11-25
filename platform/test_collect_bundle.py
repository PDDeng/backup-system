import datetime,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import collect_bundle
class SourceGates(unittest.TestCase):
 def make(self,age=0,missing=False,verification='native_ok'):
  root=Path(tempfile.mkdtemp(prefix='bundle-source-test-',dir='/var/lib/core-backup-ops/module-tests'))
  date=(datetime.date.today()-datetime.timedelta(days=age)).isoformat();run='RMAN'+date.replace('-','')+'T020000';p=root/'asset'/date/run;p.mkdir(parents=True)
  r=dict(schema='core-backup-bundle/1.0',owner='producer',asset='asset',run=run,date=date,files={'full.bkp':{'size':3}},verification=verification)
  (p/'complete.json').write_text(json.dumps(r))
  if not missing:(p/'full.bkp').write_bytes(b'abc')
  c=dict(source={'mount':str(root),'source':'test'},asset='asset',required_owner='producer',required_verification='native_ok',max_age_days=2)
  return c,p
 @patch('collect_bundle.exact_mount')
 def test_complete_source(self,_):
  c,p=self.make();self.assertEqual(collect_bundle.choose(c)[0],p)
 @patch('collect_bundle.exact_mount')
 def test_missing_file_rejected(self,_):
  c,p=self.make(missing=True)
  with self.assertRaises(ValueError):collect_bundle.choose(c)
 @patch('collect_bundle.exact_mount')
 def test_stale_source_rejected(self,_):
  c,p=self.make(age=3)
  with self.assertRaises(ValueError):collect_bundle.choose(c)
 @patch('collect_bundle.exact_mount')
 def test_unverified_source_rejected(self,_):
  c,p=self.make(verification='pending')
  with self.assertRaises(ValueError):collect_bundle.choose(c)
 @patch('collect_bundle.exact_mount')
 def test_unlisted_file_rejected(self,_):
  c,p=self.make();(p/'unexpected.bkp').write_bytes(b'unlisted')
  with self.assertRaises(ValueError):collect_bundle.choose(c)

class NativeProtocol(unittest.TestCase):
 def test_run_styles_are_explicit(self):
  self.assertTrue(collect_bundle.valid_run_name('RMAN20261124T020000'))
  self.assertFalse(collect_bundle.valid_run_name('20261124_020000-ab12'))
  self.assertTrue(collect_bundle.valid_run_name('20261124_020000-ab12','oracle_logical'))
  self.assertFalse(collect_bundle.valid_run_name('.20261124_020000-ab12.partial','oracle_logical'))
  with self.assertRaises(ValueError):collect_bundle.valid_run_name('anything','.*')
 def test_legacy_explicit_mapping(self):
  self.assertEqual(collect_bundle.native_evidence({'verification':'RMAN VALIDATE BACKUPSET (all logged pieces); transfer size_only'}),'all_logged_RMAN_pieces_validated')
 def test_no_loose_success_mapping(self):
  self.assertEqual(collect_bundle.native_evidence({'verification':'RMAN pending validation'}),'RMAN pending validation')
class TimingEvidence(unittest.TestCase):
 def test_receipt_backup_seconds(self):
  self.assertEqual(collect_bundle.phase_times({'targets':[]},{'backup_seconds':12.5}),{'source_backup_seconds':12.5})
 def test_prior_run_duration_not_reused(self):
  root=Path(tempfile.mkdtemp(prefix='bundle-timing-test-',dir='/var/lib/core-backup-ops/module-tests'));p=root/'state.json';p.write_text(json.dumps(dict(Run='OTHER',Started='2026-11-24T01:00:00+08:00',Finished='2026-11-24T02:00:00+08:00')))
  self.assertNotIn('source_backup_seconds',collect_bundle.phase_times({'targets':[],'source_evidence_ref':str(p)},{'run':'CURRENT'}))
if __name__=='__main__':unittest.main()


