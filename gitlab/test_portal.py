import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from portal import Controller, Handler


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.config=root/'configs';self.config.mkdir()
        self.state=root/'source-state';self.state.mkdir()
        c={'asset':'test','host':'10.0.0.1','state_dir':str(self.state),
           'targets':[{'id':'nas243'}],'keep_days':3,'cleanup_mode':'plan'}
        (self.config/'ops185.json').write_text(json.dumps(c))
        self.controller=Controller(self.config,root/'portal')
    def tearDown(self):self.temp.cleanup()
    def test_no_report_is_unknown(self):
        j=self.controller.snapshot()['jobs'][0]
        self.assertIsNone(j['check']);self.assertIsNone(j['upload'])
        self.assertEqual(j['keep_days'],3)
    def test_all_queue_deduplicates(self):
        self.controller.action('all','check');self.controller.action('all','check')
        self.assertEqual(list(self.controller.queue),[('ops185','check')])
    def test_pending_does_not_claim_md5(self):
        (self.state/'last-result.json').write_text(json.dumps({'run':'test','verification':'pending_md5','targets':{'nas243':True}}))
        self.assertEqual(self.controller.snapshot()['jobs'][0]['last']['verification'],'pending_md5')
    def test_api_auth_and_action(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.token='test-secret';server.controller=self.controller
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)
        try:
            with self.assertRaises(HTTPError) as err:urlopen(url+'/api/jobs')
            self.assertEqual(err.exception.code,401)
            request=Request(url+'/api/jobs',headers={'Authorization':'Bearer test-secret'})
            with urlopen(request) as r:self.assertEqual(len(json.load(r)['jobs']),1)
            request=Request(url+'/api/action',data=b'{"id":"all","action":"check"}',headers={'Authorization':'Bearer test-secret','Content-Type':'application/json'})
            with urlopen(request) as r:self.assertTrue(json.load(r)['ok'])
            self.assertEqual(len(self.controller.queue),1)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
