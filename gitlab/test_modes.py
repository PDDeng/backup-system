import hashlib
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch
from distribute import Job
from md5_verify import digest

class ModeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.run='20261119T010000-123'
        self.archive='1795000000_2026_11_19_10.5.4_gitlab_backup.tar'
        self.source='/source';self.marker='/source/.core-complete/'+self.run
        self.files={self.source+'/'+self.archive:b'archive payload',self.marker+'/gitlab-config.tar.gz':b'config',self.marker+'/version-manifest.txt':b'version'}
        self.c={'asset':'test','host':'unused','user':'root','key':'unused','known_hosts':'unused',
                'state_dir':str(self.root/'state'),'backup_dir':self.source,'cleanup_mode':'delete','minimum_free_gb':0,
                'targets':[{'id':'nas','mount':str(self.root/'nas'),'source':'//unused/share'}]}
        self.base=self.root/'nas'/'test';self.base.mkdir(parents=True)
        self.notices=[];self.commands=[]
    def tearDown(self):self.temp.cleanup()
    def job(self,full):
        j=Job(dict(self.c,verify_md5=full));j.mount=lambda t:self.base
        j.notice=lambda *args:self.notices.append(args)
        def remote(command,output=None):
            self.commands.append(command);args=shlex.split(command)
            if args[0]=='find':return (self.marker+'/DONE\n').encode()
            if args[0]=='cat':
                path=args[-1]
                if path.endswith('/DONE'):return b'COPIED_STATUS=SOURCE_COMPLETE_MD5_PENDING\nASSET=test\n'
                if path.endswith('/files.tsv'):return ''.join('%s\t%d\n'%(p,len(v)) for p,v in self.files.items()).encode()
                if output:output.write(self.files[path]);return None
                return self.files[path]
            if args[0]=='nice':return (hashlib.md5(self.files[args[-1]]).hexdigest()+'  file\n').encode()
            if args[0]=='stat':return ('%d:100:1'%len(self.files[args[-1]])).encode()
            raise AssertionError(command)
        j.remote=remote;return j
    def folder(self):return self.base/'2026-11-19'/self.run
    def test_fast_never_hashes_and_runs_cleanup(self):
        j=self.job(False)
        with patch('md5_verify.digest',side_effect=AssertionError('hash called')),patch.object(j,'clean',return_value=0) as clean:
            self.assertEqual(j.execute(),0)
        clean.assert_called_once()
        self.assertTrue((self.folder()/'pending.json').exists())
        self.assertFalse((self.folder()/'complete.json').exists())
        r=json.loads((j.state/'last-result.json').read_text())
        self.assertEqual(r['verification'],'pending_md5')
        self.assertFalse(any('md5sum' in x for x in self.commands))
    def test_full_promotes_pending(self):
        self.assertEqual(self.job(False).execute(),0)
        self.assertEqual(self.job(True).execute(),0)
        self.assertTrue((self.folder()/'complete.json').exists())
        self.assertFalse((self.folder()/'pending.json').exists())
        self.assertEqual(json.loads((self.folder()/'complete.json').read_text())['verification'],'md5_verified')
    def test_tampered_pending_fails_without_deletion(self):
        self.assertEqual(self.job(False).execute(),0)
        target=self.folder()/self.archive;target.write_bytes(b'X'*len(self.files[self.source+'/'+self.archive]))
        self.assertEqual(self.job(True).execute(),1)
        self.assertTrue(target.exists())
        self.assertTrue((self.folder()/'pending.json').exists())
    def test_source_size_change_blocks_publication(self):
        j=self.job(False);old=j.remote
        j.remote=lambda cmd,output=None:b'999:100:1' if cmd.startswith('stat ') else old(cmd,output)
        with self.assertRaises(ValueError):j.execute()
        self.assertFalse(self.folder().exists())
    def test_deleted_destination_not_skipped(self):
        import shutil
        self.assertEqual(self.job(False).execute(),0)
        shutil.rmtree(self.folder())
        j=self.job(False)
        self.assertEqual(j.execute(),0)
        self.assertTrue((self.folder()/self.archive).exists())
        self.assertTrue(any('retry collection' in n[1] for n in self.notices))
    def test_existing_destination_rechecked_before_skip(self):
        self.assertEqual(self.job(True).execute(),0)
        j=self.job(True)
        self.assertEqual(j.execute(),0)
        self.assertTrue(any('Current target files rechecked' in n[1] for n in self.notices))

if __name__=='__main__':unittest.main()
