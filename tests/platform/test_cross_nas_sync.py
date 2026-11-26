import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'platform' / 'sync'))
import cross_nas_sync as sync  # noqa: E402

BOM = b'\xef\xbb\xbf'
ORACLE = 'oracle46_asset'
SVN = 'svn29_asset'
CRM = 'crm200_asset'
KINGDEE = 'kingdee252_asset'


class FakeClock(object):
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fake_guard(root_of_mount=None, fstype='cifs', source='//nas/backup', free=10 ** 13):
    def findmnt(path):
        return {'source': source, 'fstype': fstype, 'target': path}
    return sync.Guard(findmnt=findmnt, disk_free=lambda p: free)


class Base(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='cross-nas-sync-test-'))
        self.src = self.base / 'src'
        self.dst = self.base / 'dst'
        self.state = self.base / 'state'
        for p in (self.src, self.dst, self.state):
            p.mkdir()
        self.clock = FakeClock()

    def tearDown(self):
        shutil.rmtree(str(self.base))

    def job(self, asset, layout, fmt, receipt_name, owner=None, targets=None, extra=None):
        job = {'name': 'job-' + asset.split('_')[0], 'priority': 1, 'asset': asset, 'layout': layout,
               'receipt_format': fmt, 'receipt_name': receipt_name, 'source_root': str(self.src),
               'rate_limit_bytes_per_sec': 10 ** 9,
               'targets': targets or [{'name': 'nas240', 'root': str(self.dst),
                                       'mount': {'mode': 'linux-cifs', 'source': '//nas/backup'},
                                       'reserve_bytes': 0}]}
        if owner is not None:
            job['expected_owner'] = owner
        job.update(extra or {})
        return job

    def config(self, *jobs):
        return {'schema_version': 1, 'state_dir': str(self.state), 'jobs': list(jobs)}

    def run_cfg(self, cfg, dry_run=False, guard=None):
        return sync.run(sync.validate_config(cfg), dry_run=dry_run, clock=self.clock.__call__,
                        sleep=self.clock.sleep, guard=guard or fake_guard())

    def target_tree(self):
        out = []
        for dp, dn, fn in os.walk(str(self.dst)):
            for name in dn + fn:
                out.append(os.path.relpath(os.path.join(dp, name), str(self.dst)))
        return sorted(out)


class RealLayouts(Base):
    """Fixtures shaped like the real production receipts (Oracle46, SVN29, CRM200, Kingdee252)."""

    def make_oracle(self, date, run, payload):
        u = self.src / ORACLE / date / run
        u.mkdir(parents=True)
        for name, data in payload.items():
            (u / name).write_bytes(data)
        receipt = {'schema': 'core-backup-bundle/1.0', 'asset': ORACLE, 'run': run, 'date': date,
                   'files': {n: {'size': len(d)} for n, d in payload.items()}}
        (u / 'complete.json').write_bytes(json.dumps(receipt).encode('utf-8'))
        return u

    def test_oracle46_date_run_nested_layout(self):
        self.make_oracle('2026-11-25', '20261125T020000-aaaaaa', {'expdp_full.dmp': b'D' * 4096, 'expdp.log': b'ok'})
        self.make_oracle('2026-11-26', '20261126T020000-bbbbbb', {'expdp_full.dmp': b'E' * 2048})
        # incomplete run and partial run must never be copied
        incomplete = self.src / ORACLE / '2026-11-26' / '20261126T030000-cccccc'
        incomplete.mkdir()
        (incomplete / 'expdp_full.dmp').write_bytes(b'half')
        partial = self.src / ORACLE / '2026-11-26' / '.20261126T040000-dddddd.partial'
        partial.mkdir()
        (partial / 'complete.json').write_bytes(b'{}')
        cfg = self.config(self.job(ORACLE, 'date_run', 'core-bundle-1.0', 'complete.json', owner=None,
                                   extra={'name': 'oracle46'}))
        r = self.run_cfg(cfg)
        self.assertEqual(r['status'], 'ok', r)
        copied = sorted(r['results'][0]['copied'])
        self.assertEqual(copied, ['2026-11-25/20261125T020000-aaaaaa', '2026-11-26/20261126T020000-bbbbbb'])
        out = self.dst / ORACLE / '2026-11-25' / '20261125T020000-aaaaaa'
        self.assertEqual((out / 'expdp_full.dmp').read_bytes(), b'D' * 4096)
        self.assertTrue((out / 'complete.json').exists())
        self.assertFalse((self.dst / ORACLE / '2026-11-26' / '20261126T030000-cccccc').exists())
        self.assertEqual(r['results'][0]['incomplete_source_units'], ['2026-11-26/20261126T030000-cccccc'])

    def test_oracle46_rejects_wrong_schema(self):
        u = self.make_oracle('2026-11-25', '20261125T020000-aaaaaa', {'a.dmp': b'x'})
        receipt = json.loads((u / 'complete.json').read_bytes())
        receipt['schema'] = 'something-else/1.0'
        (u / 'complete.json').write_bytes(json.dumps(receipt).encode('utf-8'))
        r = self.run_cfg(self.config(self.job(ORACLE, 'date_run', 'core-bundle-1.0', 'complete.json',
                                              extra={'name': 'oracle46'})))
        self.assertEqual(r['status'], 'errors')
        self.assertEqual(self.target_tree(), [])

    def make_svn(self, date, run):
        u = self.src / SVN / date / run
        (u / 'repo' / '子目录').mkdir(parents=True)
        (u / 'repo' / '子目录' / '中文 文件(1).txt').write_bytes('中文内容'.encode('utf-8'))
        (u / 'repo' / 'db').mkdir()
        (u / 'repo' / 'db' / 'rev.bin').write_bytes(b'\x01\x02\x03')
        (u / 'repo' / '空目录').mkdir()
        receipt = {'Owner': 'svn29-hotcopy-v1', 'Asset': SVN, 'Run': run, 'Date': date,
                   'Status': 'hotcopy_verified', 'SmallTest': False,
                   'Files': [{'Name': 'repo\\子目录\\中文 文件(1).txt', 'Bytes': len('中文内容'.encode('utf-8'))},
                             {'Name': 'repo\\db\\rev.bin', 'Bytes': 3}],
                   'Directories': ['repo\\空目录', 'repo\\db']}
        (u / 'hotcopy-result.json').write_bytes(json.dumps(receipt, ensure_ascii=False).encode('utf-8'))
        return u

    def test_svn29_pascal_receipt_nested_backslash_unicode_and_empty_dirs(self):
        self.make_svn('2026-11-25', '20261125T010000-svnsvn')
        cfg = self.config(self.job(SVN, 'date_run', 'svn-pascal', 'hotcopy-result.json', owner='svn29-hotcopy-v1',
                                   extra={'name': 'svn29'}))
        r = self.run_cfg(cfg)
        self.assertEqual(r['status'], 'ok', r)
        out = self.dst / SVN / '2026-11-25' / '20261125T010000-svnsvn'
        self.assertEqual((out / 'repo' / '子目录' / '中文 文件(1).txt').read_bytes(), '中文内容'.encode('utf-8'))
        self.assertEqual((out / 'repo' / 'db' / 'rev.bin').read_bytes(), b'\x01\x02\x03')
        self.assertTrue((out / 'repo' / '空目录').is_dir())
        self.assertEqual(os.listdir(str(out / 'repo' / '空目录')), [])

    def test_svn29_smalltest_and_bad_status_refused(self):
        u = self.make_svn('2026-11-25', '20261125T010000-svnsvn')
        receipt = json.loads((u / 'hotcopy-result.json').read_bytes().decode('utf-8'))
        receipt['SmallTest'] = True
        (u / 'hotcopy-result.json').write_bytes(json.dumps(receipt).encode('utf-8'))
        r = self.run_cfg(self.config(self.job(SVN, 'date_run', 'svn-pascal', 'hotcopy-result.json',
                                              owner='svn29-hotcopy-v1', extra={'name': 'svn29'})))
        self.assertEqual(r['status'], 'errors')
        self.assertEqual(self.target_tree(), [])
        receipt['SmallTest'] = False
        receipt['Status'] = 'running'
        (u / 'hotcopy-result.json').write_bytes(json.dumps(receipt).encode('utf-8'))
        r = self.run_cfg(self.config(self.job(SVN, 'date_run', 'svn-pascal', 'hotcopy-result.json',
                                              owner='svn29-hotcopy-v1', extra={'name': 'svn29'})))
        self.assertEqual(r['status'], 'errors')

    def test_crm200_bom_single_file_bytes_schema(self):
        u = self.src / CRM / '2026-11-25'
        u.mkdir(parents=True)
        (u / 'crm200.dmp').write_bytes(b'C' * 1500)
        (u / 'complete.json').write_bytes(BOM + json.dumps({'file': 'crm200.dmp', 'bytes': 1500}).encode('utf-8'))
        r = self.run_cfg(self.config(self.job(CRM, 'date_dir', 'crm-single', 'complete.json', extra={'name': 'crm200'})))
        self.assertEqual(r['status'], 'ok', r)
        self.assertEqual(r['results'][0]['copied'], ['2026-11-25'])
        self.assertEqual((self.dst / CRM / '2026-11-25' / 'crm200.dmp').read_bytes(), b'C' * 1500)
        self.assertEqual((self.dst / CRM / '2026-11-25' / 'complete.json').read_bytes(),
                         BOM + json.dumps({'file': 'crm200.dmp', 'bytes': 1500}).encode('utf-8'))

    def test_kingdee252_bom_names_with_spaces_parentheses_and_unicode(self):
        name = 'Kingdee 01 (账套) 2026-11-25.ADF'
        u = self.src / KINGDEE / '2026-11-25'
        u.mkdir(parents=True)
        (u / name).write_bytes(b'K' * 777)
        body = {'owner': 'kingdee-nas-v1', 'asset': KINGDEE, 'date': '2026-11-25',
                'files': [{'name': name, 'bytes': 777}]}
        (u / 'complete.json').write_bytes(BOM + json.dumps(body, ensure_ascii=False).encode('utf-8'))
        r = self.run_cfg(self.config(self.job(KINGDEE, 'date_dir', 'kingdee-list', 'complete.json',
                                              owner='kingdee-nas-v1', extra={'name': 'kingdee252'})))
        self.assertEqual(r['status'], 'ok', r)
        self.assertEqual((self.dst / KINGDEE / '2026-11-25' / name).read_bytes(), b'K' * 777)

    def test_kingdee_existing_target_identical_is_skipped_after_full_verification(self):
        name = 'Kingdee 02 (账套).ADF'
        u = self.src / KINGDEE / '2026-11-25'
        u.mkdir(parents=True)
        (u / name).write_bytes(b'K' * 10)
        (u / 'complete.json').write_bytes(BOM + json.dumps({'owner': 'kingdee-nas-v1', 'asset': KINGDEE,
                                                            'date': '2026-11-25',
                                                            'files': [{'name': name, 'bytes': 10}]}).encode('utf-8'))
        cfg = self.config(self.job(KINGDEE, 'date_dir', 'kingdee-list', 'complete.json',
                                   owner='kingdee-nas-v1', extra={'name': 'kingdee252'}))
        self.run_cfg(cfg)
        r = self.run_cfg(cfg)
        self.assertEqual(r['results'][0]['skipped'], ['2026-11-25'])
        self.assertEqual(r['results'][0]['errors'], [])


class PublishAndVerify(Base):
    def make_plain(self, date, payload):
        u = self.src / ORACLE / date
        u.mkdir(parents=True)
        for n, d in payload.items():
            (u / n).write_bytes(d)
        (u / 'complete.json').write_bytes(json.dumps({'schema': 'core-backup-bundle/1.0', 'asset': ORACLE,
                                                      'date': date, 'files': {n: {'size': len(d)} for n, d in payload.items()}}).encode('utf-8'))
        return u

    def cfg(self, **job_extra):
        return self.config(self.job(ORACLE, 'date_dir', 'core-bundle-1.0', 'complete.json',
                                    extra=dict({'name': 'oracle46'}, **job_extra)))

    def test_existing_empty_target_is_conflict_never_skipped_or_overwritten(self):
        self.make_plain('2026-11-25', {'a.dmp': b'abc'})
        empty = self.dst / ORACLE / '2026-11-25'
        empty.mkdir(parents=True)
        r = self.run_cfg(self.cfg())
        self.assertEqual(r['status'], 'errors')
        self.assertEqual(r['results'][0]['skipped'], [])
        self.assertTrue(empty.is_dir())
        self.assertEqual(os.listdir(str(empty)), [])

    def test_existing_target_with_wrong_size_is_conflict(self):
        self.make_plain('2026-11-25', {'a.dmp': b'abc'})
        self.run_cfg(self.cfg())
        (self.dst / ORACLE / '2026-11-25' / 'a.dmp').write_bytes(b'ab')
        r = self.run_cfg(self.cfg())
        self.assertEqual(r['status'], 'errors')
        self.assertIn('size mismatch', ''.join(r['results'][0]['errors']))
        self.assertEqual((self.dst / ORACLE / '2026-11-25' / 'a.dmp').read_bytes(), b'ab')

    def test_existing_target_with_missing_payload_is_conflict(self):
        self.make_plain('2026-11-25', {'a.dmp': b'abc'})
        self.run_cfg(self.cfg())
        (self.dst / ORACLE / '2026-11-25' / 'a.dmp').unlink()
        r = self.run_cfg(self.cfg())
        self.assertEqual(r['status'], 'errors')
        self.assertEqual(r['results'][0]['skipped'], [])

    def test_publish_no_replace_refuses_existing_destination(self):
        src = self.base / 'part'
        dst = self.base / 'final'
        src.mkdir()
        dst.mkdir()
        (src / 'x').write_bytes(b'new')
        with self.assertRaises(FileExistsError):
            sync.publish_no_replace(str(src), str(dst))
        self.assertTrue((src / 'x').exists())
        self.assertEqual(os.listdir(str(dst)), [])

    def test_source_payload_size_changed_publishes_nothing(self):
        d = self.make_plain('2026-11-25', {'a.dmp': b'abcde'})
        (d / 'a.dmp').write_bytes(b'ab')
        r = self.run_cfg(self.cfg())
        self.assertEqual(r['status'], 'errors')
        self.assertEqual(self.target_tree(), [])

    def test_sha256_mismatch_keeps_partial_and_publishes_nothing(self):
        d = self.make_plain('2026-11-25', {'a.dmp': b'good'})
        rec = json.loads((d / 'complete.json').read_bytes())
        rec['files']['a.dmp']['sha256'] = sha(b'other')
        (d / 'complete.json').write_bytes(json.dumps(rec).encode('utf-8'))
        r = self.run_cfg(self.cfg())
        self.assertEqual(r['status'], 'errors')
        names = os.listdir(str(self.dst / ORACLE))
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith('.partial'))

    def test_unsafe_payload_paths_rejected_and_nothing_written_outside(self):
        for bad in ('..\\escape.bin', '../escape.bin', '/abs.bin', 'C:evil.bin', 'a//b.bin', '.'):
            with self.assertRaises(sync.SyncError):
                sync.safe_rel(bad)
        self.assertEqual(sync.safe_rel('repo\\子目录\\x.txt'), 'repo/子目录/x.txt')

    def test_link_in_source_refused(self):
        self.make_plain('2026-11-25', {'a.dmp': b'abc'})
        with mock.patch.object(sync.os.path, 'islink', side_effect=lambda p: os.path.basename(p) == 'a.dmp' or False):
            r = self.run_cfg(self.cfg())
        self.assertEqual(r['status'], 'errors')
        self.assertEqual(self.target_tree(), [])


class Safety(Base):
    def cfg(self, mount=None, reserve=0):
        return self.config(self.job(ORACLE, 'date_dir', 'generic', 'complete.json', extra={'name': 'oracle46'},
                                    targets=[{'name': 'nas240', 'root': str(self.dst),
                                              'mount': mount or {'mode': 'linux-cifs', 'source': '//nas/backup'},
                                              'reserve_bytes': reserve}]))

    def make(self):
        u = self.src / ORACLE / '2026-11-25'
        u.mkdir(parents=True)
        (u / 'a.dmp').write_bytes(b'abc')
        (u / 'complete.json').write_bytes(json.dumps({'owner': 'x', 'asset': ORACLE, 'date': '2026-11-25',
                                                      'files': {'a.dmp': {'size': 3}}}).encode('utf-8'))

    def test_unmounted_mountpoint_refused_and_nothing_created(self):
        self.make()
        g = sync.Guard(findmnt=lambda p: None, disk_free=lambda p: 10 ** 13)
        r = self.run_cfg(self.cfg(), guard=g)
        self.assertEqual(r['status'], 'errors')
        self.assertFalse((self.dst / ORACLE).exists())

    def test_wrong_fstype_or_source_refused(self):
        self.make()
        for guard in (fake_guard(fstype='ext4'), fake_guard(source='//other/share')):
            r = self.run_cfg(self.cfg(), guard=guard)
            self.assertEqual(r['status'], 'errors')
        self.assertEqual(self.target_tree(), [])

    def test_mountpoint_that_is_only_parent_fs_refused(self):
        self.make()
        g = sync.Guard(findmnt=lambda p: {'source': '//nas/backup', 'fstype': 'cifs', 'target': str(self.base)},
                       disk_free=lambda p: 10 ** 13)
        r = self.run_cfg(self.cfg(), guard=g)
        self.assertEqual(r['status'], 'errors')

    def test_reserve_space_guard_blocks_before_any_write(self):
        self.make()
        g = fake_guard(free=100)
        r = self.run_cfg(self.cfg(reserve=1000), guard=g)
        self.assertEqual(r['status'], 'errors')
        self.assertIn('free space', ''.join(r['results'][0]['errors']))
        self.assertEqual(self.target_tree(), [])

    def test_oserror_in_one_unit_does_not_stop_other_units_or_result(self):
        self.make()
        u2 = self.src / ORACLE / '2026-11-26'
        u2.mkdir(parents=True)
        (u2 / 'b.dmp').write_bytes(b'bb')
        (u2 / 'complete.json').write_bytes(json.dumps({'owner': 'x', 'asset': ORACLE, 'date': '2026-11-26',
                                                       'files': {'b.dmp': {'size': 2}}}).encode('utf-8'))
        real_copy = sync.copy_file

        def flaky(src, dst, size, sha256, limiter):
            if src.endswith('b.dmp'):
                raise OSError(5, 'simulated SMB I/O error')
            return real_copy(src, dst, size, sha256, limiter)
        with mock.patch.object(sync, 'copy_file', side_effect=flaky):
            r = self.run_cfg(self.cfg())
        self.assertEqual(r['results'][0]['copied'], ['2026-11-25'])
        self.assertTrue(any('simulated SMB' in e for e in r['results'][0]['errors']))

    def test_unexpected_target_exception_is_recorded_and_batch_continues(self):
        self.make()
        cfg = self.config(self.job(ORACLE, 'date_dir', 'generic', 'complete.json', extra={'name': 'oracle46'},
                                   targets=[{'name': 'nas240', 'root': str(self.dst),
                                             'mount': {'mode': 'linux-cifs', 'source': '//nas/backup'},
                                             'reserve_bytes': 0},
                                            {'name': 'other', 'root': str(self.base / 'other'),
                                             'mount': {'mode': 'linux-cifs', 'source': '//nas/x'},
                                             'reserve_bytes': 0}]))
        calls = {'n': 0}
        real = sync.run_target

        def boom(*a, **k):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('unexpected')
            return real(*a, **k)
        with mock.patch.object(sync, 'run_target', side_effect=boom):
            r = self.run_cfg(cfg, guard=fake_guard())
        self.assertEqual(len(r['results']), 2)
        self.assertIn('unexpected RuntimeError', r['results'][0]['errors'][0])

    def test_main_always_writes_result_on_unexpected_failure(self):
        self.make()
        cfg_path = self.base / 'cfg.json'
        cfg_path.write_text(json.dumps(self.cfg()), encoding='utf-8')
        out = self.base / 'result.json'
        with mock.patch.object(sync, 'run', side_effect=RuntimeError('boom')):
            with contextlib.redirect_stdout(io.StringIO()):
                rc = sync.main(['--config', str(cfg_path), '--result', str(out)])
        self.assertEqual(rc, 1)
        data = json.loads(out.read_text('utf-8'))
        self.assertIn('boom', data['fatal'])


class LockSafety(Base):
    def test_busy_lock_reports_and_does_not_copy(self):
        u = self.src / ORACLE / '2026-11-25'
        u.mkdir(parents=True)
        (u / 'a.dmp').write_bytes(b'a')
        (u / 'complete.json').write_bytes(json.dumps({'owner': 'x', 'asset': ORACLE, 'date': '2026-11-25',
                                                      'files': {'a.dmp': {'size': 1}}}).encode('utf-8'))
        cfg = self.config(self.job(ORACLE, 'date_dir', 'generic', 'complete.json', extra={'name': 'oracle46'}))
        held = sync.RunLock(str(self.state), 'oracle46', 'nas240')
        held.acquire()
        try:
            r = self.run_cfg(cfg, guard=fake_guard())
        finally:
            held.release()
        self.assertEqual(r['status'], 'errors')
        self.assertIn('another run holds', ''.join(r['results'][0]['errors']))
        self.assertEqual(self.target_tree(), [])
        after = self.run_cfg(cfg, guard=fake_guard())
        self.assertEqual(after['status'], 'ok')

    def test_lock_names_are_validated_and_do_not_collide(self):
        self.assertNotEqual(sync.RunLock(str(self.state), 'a-b', 'c').path,
                            sync.RunLock(str(self.state), 'a', 'b-c').path)
        for bad in ('a/b', '..', 'a.b', 'x y', ''):
            with self.assertRaises(sync.SyncError):
                sync.RunLock(str(self.state), bad, 'nas240')

    def test_lock_released_after_run_so_next_run_is_free(self):
        u = self.src / ORACLE / '2026-11-25'
        u.mkdir(parents=True)
        (u / 'a.dmp').write_bytes(b'a')
        (u / 'complete.json').write_bytes(json.dumps({'owner': 'x', 'asset': ORACLE, 'date': '2026-11-25',
                                                      'files': {'a.dmp': {'size': 1}}}).encode('utf-8'))
        cfg = self.config(self.job(ORACLE, 'date_dir', 'generic', 'complete.json', extra={'name': 'oracle46'}))
        self.assertEqual(self.run_cfg(cfg, guard=fake_guard())['status'], 'ok')
        self.assertEqual(self.run_cfg(cfg, guard=fake_guard())['status'], 'ok')


class ThrottleAndConfig(Base):
    def test_rate_limit_sleeps_so_bytes_do_not_run_ahead(self):
        lim = sync.RateLimiter(1000, clock=self.clock, sleep=self.clock.sleep)
        lim.consume(3000)
        self.assertAlmostEqual(sum(self.clock.slept), 3.0, places=6)

    def test_burst_credit_is_capped_after_a_stall(self):
        lim = sync.RateLimiter(1000, clock=self.clock, sleep=self.clock.sleep)
        lim.consume(100)
        self.clock.now = 100.0
        lim.consume(3000)
        self.assertAlmostEqual(sum(self.clock.slept), 0.1 + 2.0, places=6)

    def test_no_sleep_when_already_behind_schedule(self):
        lim = sync.RateLimiter(1000, clock=self.clock, sleep=self.clock.sleep)
        lim.consume(100)
        self.clock.now = 10.0
        lim.consume(100)
        self.assertAlmostEqual(sum(self.clock.slept), 0.1, places=6)


class ConfigGuards(unittest.TestCase):
    def base(self, **over):
        job = {'name': 'j', 'asset': 'a', 'layout': 'date_dir', 'receipt_format': 'generic',
               'source_root': os.path.abspath('src-root'), 'rate_limit_bytes_per_sec': 10,
               'targets': [{'name': 't', 'root': os.path.abspath('dst-root'),
                            'mount': {'mode': 'linux-cifs', 'source': '//nas/x'}}]}
        job.update(over)
        return {'schema_version': 1, 'state_dir': os.path.abspath('state'), 'jobs': [job]}

    def test_valid_config_normalizes_and_orders_by_priority(self):
        cfg = self.base()
        cfg['jobs'].append({'name': 'first', 'priority': 0, 'asset': 'b', 'layout': 'date_run',
                            'receipt_format': 'core-bundle-1.0', 'source_root': os.path.abspath('s2'),
                            'rate_limit_bytes_per_sec': 5,
                            'targets': [{'name': 'x', 'root': os.path.abspath('d2'),
                                         'mount': {'mode': 'linux-cifs', 'source': '//n/y'}}]})
        out = sync.validate_config(cfg)
        self.assertEqual([j['name'] for j in out['jobs']], ['first', 'j'])

    def test_placeholder_in_any_string_refused_including_owner_and_result_file(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(expected_owner='CHANGE_ME_OWNER'))
        cfg = self.base()
        cfg['result_file'] = 'CHANGE_ME_RESULT.json'
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(cfg)
        cfg = self.base()
        cfg['state_dir'] = 'CHANGE_ME_STATE'
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(cfg)

    def test_unsafe_job_or_target_names_refused(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(name='a.b'))
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(name='../x'))

    def test_unknown_format_layout_mount_refused(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(receipt_format='mystery'))
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(layout='flat'))
        cfg = self.base()
        cfg['jobs'][0]['targets'][0]['mount'] = {'mode': 'none'}
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(cfg)

    def test_overlapping_and_duplicate_targets_refused(self):
        cfg = self.base()
        cfg['jobs'][0]['targets'][0]['root'] = os.path.join(os.path.abspath('src-root'), 'inside')
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(cfg)
        cfg = self.base()
        cfg['jobs'][0]['targets'].append(dict(cfg['jobs'][0]['targets'][0], root=os.path.abspath('d2')))
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(cfg)

    def test_windows_unc_requires_unc_root_and_linux_cifs_requires_absolute(self):
        cfg = self.base()
        cfg['jobs'][0]['targets'][0]['mount'] = {'mode': 'windows-unc'}
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(cfg)
        cfg = self.base()
        cfg['jobs'][0]['targets'][0]['root'] = 'relative/dir'
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(cfg)


class ExampleConfigs(unittest.TestCase):
    NAMES = ['oracle46-nas243-to-nas240.example.json', 'svn29.example.json',
             'crm200.example.json', 'kingdee252.example.json']

    def fixture(self, name):
        data = json.loads((ROOT / 'platform' / 'sync' / 'examples' / name).read_bytes().decode('utf-8-sig'))
        counter = {'n': 0}

        # walk and replace strings in place
        def replace(obj, key=None):
            if isinstance(obj, dict):
                for k in list(obj):
                    v = obj[k]
                    if isinstance(v, str) and 'CHANGE_ME' in v:
                        counter['n'] += 1
                        n = counter['n']
                        if k == 'source':
                            obj[k] = '//fixture-%d/share' % n
                        elif k == 'mode':
                            obj[k] = v
                        elif k == 'root' and obj.get('mount', {}).get('mode') == 'windows-unc':
                            obj[k] = '\\\\fixture-%d\\share' % n
                        else:
                            obj[k] = os.path.join(tempfile.gettempdir(), 'fixture-%d' % n)
                    else:
                        replace(v, k)
            elif isinstance(obj, list):
                for item in obj:
                    replace(item)
        replace(data)
        return data

    def test_four_redacted_examples_exist_with_placeholders_and_refuse_to_run(self):
        for name in self.NAMES:
            p = ROOT / 'platform' / 'sync' / 'examples' / name
            self.assertTrue(p.exists(), name)
            self.assertIn('CHANGE_ME', p.read_text(encoding='utf-8'), name)
            with self.assertRaises(sync.ConfigError):
                sync.load_config(str(p))

    def test_examples_are_valid_once_placeholders_are_replaced(self):
        for name in self.NAMES:
            cfg = sync.validate_config(self.fixture(name))
            self.assertTrue(cfg['jobs'], name)

    def test_example_formats_match_real_layouts(self):
        formats = {}
        for name in self.NAMES:
            cfg = sync.validate_config(self.fixture(name))
            formats[name] = [(j['layout'], j['receipt_format'], j['receipt_name']) for j in cfg['jobs']]
        self.assertEqual(formats['oracle46-nas243-to-nas240.example.json'], [('date_run', 'core-bundle-1.0', 'complete.json')])
        self.assertEqual(formats['svn29.example.json'], [('date_run', 'svn-pascal', 'hotcopy-result.json')])
        self.assertEqual(formats['kingdee252.example.json'], [('date_dir', 'kingdee-list', 'complete.json')])
        self.assertEqual(formats['crm200.example.json'], [('date_dir', 'crm-single', 'complete.json')])


if __name__ == '__main__':
    unittest.main()
