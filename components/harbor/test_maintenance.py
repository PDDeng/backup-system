import json
from pathlib import Path
import tempfile
import unittest
from maintenance import Maintenance


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'state.json'
        self.ro=False;self.paused=False;self.calls=[];self.fail_resume=False;self.busy=False
        def api(path,method='GET',body=None):
            self.calls.append((path,method,body))
            if path=='/configurations':
                if method=='PUT':self.ro=body['read_only'];return None
                return {'read_only':{'value':self.ro}}
            if path=='/schedules/all/paused':return {'paused':False}
            if path=='/jobservice/queues':return [{'job_type':'GC','paused':self.paused}]
            if path=='/jobservice/queues/all':
                if body['action']=='resume' and self.fail_resume:raise IOError('API offline')
                self.paused=body['action']=='pause';return None
            if path=='/jobservice/pools/all/workers':return [{'job_id':'busy'}] if self.busy else [{'job_id':'','job_name':''}]
            raise AssertionError(path)
        self.api=api
        self.guard=Maintenance(api,self.path,lambda p,v:p.write_text(json.dumps(v)),lambda m:None,timeout=-1,sleep=lambda n:None)
    def tearDown(self):self.temp.cleanup()
    def test_queue_pause_precedes_readonly(self):
        self.guard.enter()
        writes=[p for p,m,b in self.calls if m=='PUT']
        self.assertEqual(writes[:2],['/jobservice/queues/all','/configurations'])
    def test_normal_restore(self):
        self.guard.enter();self.assertTrue(self.ro);self.assertTrue(self.paused)
        self.guard.restore();self.assertFalse(self.ro);self.assertFalse(self.paused)
        self.assertFalse(json.loads(self.path.read_text())['active'])
    def test_original_readonly_preserved(self):
        self.ro=True
        with self.assertRaises(ValueError):self.guard.enter()
        self.assertTrue(self.ro);self.assertFalse(self.paused)
    def test_original_paused_preserved(self):
        self.paused=True;self.guard.enter();self.guard.restore();self.assertTrue(self.paused);self.assertFalse(self.ro)
    def test_busy_timeout_restores(self):
        self.busy=True
        with self.assertRaises(ValueError):self.guard.enter()
        self.guard.restore();self.assertFalse(self.ro);self.assertFalse(self.paused)
    def test_failure_after_request_mutation_restores(self):
        old=self.api
        def failed(path,method='GET',body=None):
            result=old(path,method,body)
            if path=='/configurations' and method=='PUT' and body['read_only']:raise IOError('Lost HTTP response')
            return result
        self.guard.api=failed
        with self.assertRaises(IOError):self.guard.enter()
        self.guard.api=self.api;self.guard.restore();self.assertFalse(self.ro)
    def test_failed_restore_stays_actionable(self):
        self.guard.enter();self.fail_resume=True
        with self.assertRaises(RuntimeError):self.guard.restore()
        self.assertTrue(json.loads(self.path.read_text())['active'])
        self.fail_resume=False;self.guard.state=None;self.guard.restore();self.assertFalse(self.paused)
    def test_no_changes_by_inspect(self):
        self.guard.inspect();self.assertTrue(all(m=='GET' for _,m,_ in self.calls));self.assertFalse(self.path.exists())
    def test_old_active_state_blocks_new_run(self):
        self.path.write_text('{"active":true}')
        with self.assertRaises(ValueError):self.guard.enter()
        self.assertFalse(self.calls)
    def test_mixed_pause_state_no_mutation(self):
        old=self.api
        self.guard.api=lambda p,m='GET',b=None:[{'job_type':'GC','paused':True},{'job_type':'REPLICATION','paused':False}] if p=='/jobservice/queues' else old(p,m,b)
        with self.assertRaises(ValueError):self.guard.enter()
        self.assertTrue(all(m=='GET' for _,m,_ in self.calls))


if __name__=='__main__':unittest.main()
