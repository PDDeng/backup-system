import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import notify


class NotifyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'notify.json'
        self.path.write_text(json.dumps({'Enabled':True,'SummaryOnly':True,
            'WebhookUrl':'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=REPLACE_ME'}))
    def tearDown(self):self.temp.cleanup()
    def test_progress_does_not_send(self):
        for event in ('START','INFO','SUCCESS','SKIP','FAILURE'):
            with patch('sys.argv',['notify.py',str(self.path),event,'test','phase']),patch.object(notify,'urlopen') as send:
                self.assertEqual(notify.main(),0);send.assert_not_called()
    def test_summary_sends_exact_text(self):
        with patch('sys.argv',['notify.py',str(self.path),'SUMMARY','test','FINAL RESULT']),patch.object(notify,'urlopen',return_value=io.BytesIO(b'{"errcode":0}')) as send:
            self.assertEqual(notify.main(),0)
            body=json.loads(send.call_args[0][0].data)
            self.assertEqual(body['text']['content'],'FINAL RESULT')
    def test_critical_still_sends(self):
        with patch('sys.argv',['notify.py',str(self.path),'CRITICAL','test','RESTORE FAILED']),patch.object(notify,'urlopen',return_value=io.BytesIO(b'{"errcode":0}')) as send:
            self.assertEqual(notify.main(),0);send.assert_called_once()
    def test_long_text_is_bounded(self):
        with patch('sys.argv',['notify.py',str(self.path),'SUMMARY','test','备份结果'*2000]),patch.object(notify,'urlopen',return_value=io.BytesIO(b'{"errcode":0}')) as send:
            self.assertEqual(notify.main(),0)
            self.assertLess(len(json.loads(send.call_args[0][0].data)['text']['content'].encode('utf-8')),1900)


if __name__=='__main__':unittest.main()
