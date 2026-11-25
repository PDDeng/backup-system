import datetime,json,tempfile,unittest
from pathlib import Path
from source_adapters import legacy_dse
class DseSourceTests(unittest.TestCase):
 def fixture(self):
  root=Path(tempfile.mkdtemp(prefix='dse-source-test-',dir='/var/lib/core-backup-ops/module-tests'));date=datetime.date.today().isoformat();p=root/'asset'/date;p.mkdir(parents=True);name=date+' 00-00-01.bak';(p/name).write_bytes(b'abc')
  r=dict(owner='dse-nas-v1',asset='asset',date=date,files=[{'name':name,'bytes':3}],finished='2026-11-24T01:00:00+08:00');(p/'complete.json').write_text(json.dumps(r));c=dict(asset='asset',source={'mount':str(root)},required_owner='dse-standard-adapter-v1',required_verification='not_provided_by_source',max_age_days=9);return c,p,r
 def test_valid_legacy_adapted_without_native_claim(self):
  c,p,r=self.fixture();source,out=legacy_dse(c);self.assertEqual(source,p);self.assertEqual(out['native_verification'],'not_provided_by_source');self.assertEqual(out['verification'],'size_only');self.assertEqual(json.loads((p/'complete.json').read_text()),r)
 def test_size_mismatch_rejected(self):
  c,p,r=self.fixture();r['files'][0]['bytes']=4;(p/'complete.json').write_text(json.dumps(r))
  with self.assertRaises(ValueError):legacy_dse(c)
 def test_extra_file_rejected(self):
  c,p,r=self.fixture();(p/'unknown.tmp').write_bytes(b'x')
  with self.assertRaises(ValueError):legacy_dse(c)
if __name__=='__main__':unittest.main()
