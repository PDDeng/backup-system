import runpy
from pathlib import Path
import unittest
from unittest.mock import patch
import struct

M=runpy.run_path(str(Path(__file__).with_name('time-agent.py')))
G=M['main'].__globals__
class Tests(unittest.TestCase):
    def test_old_timezone_fallback(self):
        self.assertEqual(M['timezone_value']('', ' Time zone: Asia/Shanghai (CST, +0800)'), 'Timezone=Asia/Shanghai')
        self.assertEqual(M['timezone_value']('', ' Time zone: America/New_York (EST, -0500)'), 'Timezone=America/New_York')
    def test_other_ntp_service_recognized(self):
        self.assertTrue(M['synchronized']('System clock synchronized: yes\nNTP service: active'))
        self.assertTrue(M['synchronized']('NTP synchronized: yes'))
        self.assertFalse(M['synchronized']('NTP enabled: yes\nNTP synchronized: no'))
    def test_large_offset_does_not_apply(self):
        with patch.dict(G,status=lambda:{},ntp_sample=lambda host:{'offset_seconds':20},
                        apply_timezone=lambda:self.fail('timezone changed'),configure_chrony=lambda c:self.fail('chrony changed')):
            with self.assertRaisesRegex(ValueError,'safe apply'):M['main']({'ntp_servers':['a','b'],'apply':True})
    def test_sources_disagree_does_not_apply(self):
        with patch.dict(G,status=lambda:{},ntp_sample=lambda h:{'offset_seconds':0 if h=='a' else 3},
                        apply_timezone=lambda:self.fail('changed')):
            with self.assertRaisesRegex(ValueError,'disagree'):M['main']({'ntp_servers':['a','b'],'apply':True})
    def test_audit_does_not_change(self):
        with patch.dict(G,status=lambda:{'chrony_tracking':'Leap status : Normal','chrony_sources':'','timezone':'Timezone=Asia/Shanghai'},
                        ntp_sample=lambda h:{'offset_seconds':.01},apply_timezone=lambda:self.fail('changed')):
            r=M['main']({'ntp_servers':['a','b'],'apply':False});self.assertEqual(r['status'],'ok')
    def test_unreachable_refused(self):
        def fail(h):raise OSError('unreachable')
        with patch.dict(G,status=lambda:{},ntp_sample=fail):
            with self.assertRaisesRegex(ValueError,'Fewer than two'):M['main']({'ntp_servers':['a','b'],'apply':True})
    def test_invalid_ntp_packet(self):
        class Socket:
            def settimeout(self,n):pass
            def connect(self,a):pass
            def send(self,p):pass
            def recv(self,n):return b'bad'
            def close(self):pass
        with patch.object(G['socket'],'socket',return_value=Socket()):
            with self.assertRaisesRegex(ValueError,'Short'):M['ntp_sample']('example')

if __name__=='__main__':unittest.main()
