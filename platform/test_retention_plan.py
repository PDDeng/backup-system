import datetime,json,tempfile,unittest
from pathlib import Path
from retention_plan import plan
class RetentionBoundaries(unittest.TestCase):
 def setUp(self):self.root=Path(tempfile.mkdtemp(prefix='retention-plan-',dir='/var/lib/core-backup-ops/module-tests'))
 def cycle(self,offset):
  day=(datetime.date.today()-datetime.timedelta(days=offset)).isoformat();run='RMAN'+day.replace('-','')+'T020000';p=self.root/day/run;p.mkdir(parents=True);(p/'full.bkp').write_bytes(b'abc');(p/'complete.json').write_text(json.dumps({'owner':'test','asset':'asset','date':day,'run':run,'files':{'full.bkp':{'size':3}}}));return p
 def test_keep_cycles_not_age(self):
  for age in (0,10,50):self.cycle(age)
  r=plan(self.root,'asset','test',2);self.assertEqual(len(r['retained']),2);self.assertEqual(len(r['excess_complete_cycles']),1);self.assertTrue(Path(r['excess_complete_cycles'][0]['path']).exists());self.assertFalse(r['deletion_performed'])
 def test_insufficient_cycles_no_excess(self):
  self.cycle(50);self.assertEqual(plan(self.root,'asset','test',15)['excess_complete_cycles'],[])
 def test_partial_protects_entire_date(self):
  p=self.cycle(10);(p.parent/'.failed.partial').mkdir();r=plan(self.root,'asset','test',1);self.assertEqual(r['complete_cycles'],0);self.assertEqual(len(r['protected']),1)
 def test_extra_file_protected(self):
  p=self.cycle(10);(p/'foreign.txt').write_text('preserve');r=plan(self.root,'asset','test',1);self.assertEqual(r['complete_cycles'],0);self.assertTrue((p/'foreign.txt').exists())
 def test_size_mismatch_protected(self):
  p=self.cycle(10);(p/'full.bkp').write_bytes(b'ab');self.assertEqual(plan(self.root,'asset','test',1)['complete_cycles'],0)
 def test_invalid_keep_rejected(self):
  with self.assertRaises(ValueError):plan(self.root,'asset','test',0)
if __name__=='__main__':unittest.main()
