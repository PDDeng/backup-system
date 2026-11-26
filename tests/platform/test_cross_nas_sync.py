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

ASSET = 'oracle46_asset'


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


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='cross-nas-sync-test-'))
        self.src = self.base / 'src'
        self.dst = self.base / 'dst'
        self.state = self.base / 'state'
        self.src.mkdir()
        self.dst.mkdir()
        self.state.mkdir()
        self.clock = FakeClock()

    def tearDown(self):
        shutil.rmtree(str(self.base), ignore_errors=False)

    def make_date(self, date, files, owner='oracle46-v1', shape='list', with_sha=True, receipt=True):
        d = self.src / ASSET / date
        d.mkdir(parents=True)
        entries = []
        for name, data in files.items():
            (d / name).write_bytes(data)
            meta = {'size': len(data)} if shape == 'dict' else {'bytes': len(data)}
            if with_sha:
                meta['sha256'] = sha(data)
            if shape == 'dict':
                entries.append((name, meta))
            else:
                entries.append(dict(name=name, **meta))
        if receipt:
            body = {'owner': owner, 'asset': ASSET, 'date': date}
            body['files'] = dict(entries) if shape == 'dict' else entries
            (d / 'complete.json').write_text(json.dumps(body), encoding='utf-8')
        return d

    def config(self, targets=None, rate=1000000, receipt_name='complete.json', owner='oracle46-v1', extra=None):
        job = {'name': 'oracle46', 'priority': 1, 'asset': ASSET, 'source_root': str(self.src),
               'receipt_name': receipt_name, 'expected_owner': owner,
               'rate_limit_bytes_per_sec': rate,
               'targets': targets or [{'name': 'nas240', 'root': str(self.dst)}]}
        if extra:
            job.update(extra)
        return {'schema_version': 1, 'state_dir': str(self.state), 'jobs': [job]}

    def run_cfg(self, cfg, dry_run=False):
        return sync.run(sync.validate_config(cfg), dry_run=dry_run, clock=self.clock, sleep=self.clock.sleep)

    def target_dates(self):
        return sorted(p.name for p in (self.dst / ASSET).iterdir()) if (self.dst / ASSET).exists() else []


class HappyPath(SyncCase):
    def test_copies_complete_dates_with_identical_content_and_receipt(self):
        self.make_date('2026-11-24', {'a_20261124.ADF': b'alpha' * 10, 'b.bin': b'\x00\xff' * 50})
        self.make_date('2026-11-25', {'c.bin': b'gamma'})
        r = self.run_cfg(self.config())
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(sorted(r['results'][0]['copied']), ['2026-11-24', '2026-11-25'])
        self.assertEqual(self.target_dates(), ['2026-11-24', '2026-11-25'])
        out = self.dst / ASSET / '2026-11-24'
        self.assertEqual((out / 'a_20261124.ADF').read_bytes(), b'alpha' * 10)
        self.assertEqual((out / 'b.bin').read_bytes(), b'\x00\xff' * 50)
        self.assertEqual((out / 'complete.json').read_bytes(), (self.src / ASSET / '2026-11-24' / 'complete.json').read_bytes())
        self.assertFalse([p for p in (self.dst / ASSET).iterdir() if p.name.endswith('.partial')])

    def test_identical_target_is_skipped_not_rewritten(self):
        self.make_date('2026-11-24', {'a.bin': b'one'})
        self.run_cfg(self.config())
        marker = self.dst / ASSET / '2026-11-24' / 'a.bin'
        before = marker.stat().st_mtime_ns
        r = self.run_cfg(self.config())
        self.assertEqual(r['results'][0]['skipped'], ['2026-11-24'])
        self.assertEqual(r['results'][0]['copied'], [])
        self.assertEqual(marker.stat().st_mtime_ns, before)

    def test_dict_receipt_shape_used_by_platform_is_accepted(self):
        self.make_date('2026-11-24', {'full.bkp': b'abc'}, shape='dict')
        r = self.run_cfg(self.config())
        self.assertEqual(r['results'][0]['copied'], ['2026-11-24'])

    def test_receipt_without_sha256_copies_on_size_only(self):
        self.make_date('2026-11-24', {'x.bin': b'size only'}, with_sha=False)
        r = self.run_cfg(self.config())
        self.assertEqual(r['results'][0]['copied'], ['2026-11-24'])


class SourceRules(SyncCase):
    def test_incomplete_and_partial_dates_are_never_copied(self):
        self.make_date('2026-11-24', {'ok.bin': b'ok'})
        self.make_date('2026-11-25', {'partial.bin': b'no receipt'}, receipt=False)
        partial = self.src / ASSET / '.2026-11-26.abc.partial'
        partial.mkdir()
        (partial / 'complete.json').write_text('{}', encoding='utf-8')
        (self.src / ASSET / 'not-a-date').mkdir()
        r = self.run_cfg(self.config())
        self.assertEqual(r['results'][0]['copied'], ['2026-11-24'])
        self.assertEqual(r['results'][0]['incomplete_source_dates'], ['2026-11-25'])
        self.assertEqual(self.target_dates(), ['2026-11-24'])

    def test_source_size_mismatch_publishes_nothing(self):
        d = self.make_date('2026-11-24', {'a.bin': b'12345'})
        (d / 'a.bin').write_bytes(b'12')
        r = self.run_cfg(self.config())
        self.assertEqual(r['status'], 'errors')
        self.assertEqual(self.target_dates(), [])
        self.assertIn('2026-11-24', ''.join(r['results'][0]['errors']))

    def test_corrupt_hash_fails_and_keeps_partial_for_inspection(self):
        d = self.make_date('2026-11-24', {'a.bin': b'good bytes'})
        receipt = json.loads((d / 'complete.json').read_text('utf-8'))
        receipt['files'][0]['sha256'] = sha(b'other bytes')
        (d / 'complete.json').write_text(json.dumps(receipt), encoding='utf-8')
        r = self.run_cfg(self.config())
        self.assertEqual(r['status'], 'errors')
        published = [n for n in self.target_dates() if not n.endswith('.partial')]
        self.assertEqual(published, [])
        leftovers = self.target_dates()
        self.assertEqual(len(leftovers), 1)
        self.assertTrue(leftovers[0].endswith('.partial'))

    def test_receipt_date_or_asset_mismatch_rejected(self):
        d = self.make_date('2026-11-24', {'a.bin': b'x'})
        receipt = json.loads((d / 'complete.json').read_text('utf-8'))
        receipt['date'] = '2026-11-23'
        (d / 'complete.json').write_text(json.dumps(receipt), encoding='utf-8')
        r = self.run_cfg(self.config())
        self.assertEqual(r['status'], 'errors')
        self.assertEqual(self.target_dates(), [])

    def test_owner_mismatch_rejected(self):
        self.make_date('2026-11-24', {'a.bin': b'x'}, owner='someone-else')
        r = self.run_cfg(self.config(owner='oracle46-v1'))
        self.assertEqual(r['status'], 'errors')
        self.assertEqual(self.target_dates(), [])

    def test_unsafe_receipt_file_name_rejected_and_nothing_written_outside(self):
        d = self.make_date('2026-11-24', {'a.bin': b'x'})
        receipt = json.loads((d / 'complete.json').read_text('utf-8'))
        receipt['files'][0]['name'] = '..\\escape.bin'
        (d / 'complete.json').write_text(json.dumps(receipt), encoding='utf-8')
        r = self.run_cfg(self.config())
        self.assertEqual(r['status'], 'errors')
        self.assertFalse((self.base / 'escape.bin').exists())
        self.assertEqual(self.target_dates(), [])

    def test_link_in_source_is_refused(self):
        d = self.make_date('2026-11-24', {'a.bin': b'x'})
        real_is_link = sync.is_link

        def fake(path):
            return os.path.basename(path) == 'a.bin' or real_is_link(path)
        with mock.patch.object(sync, 'is_link', side_effect=fake):
            r = self.run_cfg(self.config())
        self.assertEqual(r['status'], 'errors')
        self.assertEqual(self.target_dates(), [])


class TargetRules(SyncCase):
    def test_existing_different_target_is_conflict_and_untouched(self):
        self.make_date('2026-11-24', {'a.bin': b'source version'})
        final = self.dst / ASSET / '2026-11-24'
        final.mkdir(parents=True)
        (final / 'a.bin').write_bytes(b'operator version')
        r = self.run_cfg(self.config())
        self.assertEqual(r['status'], 'errors')
        self.assertIn('not overwritten', ''.join(r['results'][0]['errors']))
        self.assertEqual((final / 'a.bin').read_bytes(), b'operator version')

    def test_add_only_nothing_deleted_on_either_side(self):
        self.make_date('2026-11-24', {'a.bin': b'a'})
        self.run_cfg(self.config())
        extra = self.dst / ASSET / 'manual-keep'
        extra.mkdir()
        (extra / 'note.txt').write_text('keep me', encoding='utf-8')
        shutil.rmtree(str(self.src / ASSET / '2026-11-24'))
        r = self.run_cfg(self.config())
        self.assertEqual(r['status'], 'ok')
        self.assertEqual((extra / 'note.txt').read_text('utf-8'), 'keep me')
        self.assertEqual(self.target_dates(), ['2026-11-24', 'manual-keep'])

    def test_missing_target_root_is_not_created(self):
        self.make_date('2026-11-24', {'a.bin': b'a'})
        missing = self.base / 'unmounted' / 'share'
        r = self.run_cfg(self.config(targets=[{'name': 'nas240', 'root': str(missing)}]))
        self.assertEqual(r['status'], 'errors')
        self.assertFalse((self.base / 'unmounted').exists())

    def test_two_targets_both_receive_the_same_dates(self):
        self.make_date('2026-11-24', {'a.bin': b'a'})
        second = self.base / 'dst2'
        second.mkdir()
        cfg = self.config(targets=[{'name': 'nas243', 'root': str(self.dst)},
                                   {'name': 'win254', 'root': str(second)}])
        r = self.run_cfg(cfg)
        self.assertEqual(r['status'], 'ok')
        self.assertEqual([x['copied'] for x in r['results']], [['2026-11-24'], ['2026-11-24']])
        self.assertTrue((second / ASSET / '2026-11-24' / 'a.bin').exists())

    def test_failed_date_does_not_stop_later_dates(self):
        d = self.make_date('2026-11-24', {'a.bin': b'bad'})
        (d / 'a.bin').write_bytes(b'b')
        self.make_date('2026-11-25', {'c.bin': b'fine'})
        r = self.run_cfg(self.config())
        self.assertEqual(r['results'][0]['copied'], ['2026-11-25'])
        self.assertEqual(self.target_dates(), ['2026-11-25'])

    def test_dry_run_writes_nothing(self):
        self.make_date('2026-11-24', {'a.bin': b'a'})
        r = self.run_cfg(self.config(), dry_run=True)
        self.assertEqual(r['results'][0]['planned'], ['2026-11-24'])
        self.assertFalse((self.dst / ASSET).exists())


class Throttle(SyncCase):
    def test_rate_limit_sleeps_so_bytes_do_not_run_ahead(self):
        self.make_date('2026-11-24', {'a.bin': b'x' * 3000})
        r = self.run_cfg(self.config(rate=1000))
        self.assertEqual(r['results'][0]['copied'], ['2026-11-24'])
        self.assertAlmostEqual(sum(self.clock.slept), 3.0, places=2)
        self.assertAlmostEqual(r['results'][0]['throttle_seconds'], 3.0, places=2)

    def test_limiter_does_not_sleep_when_already_behind_schedule(self):
        clock = FakeClock()
        lim = sync.RateLimiter(1000, clock=clock, sleep=clock.sleep)
        lim.consume(100)
        self.assertAlmostEqual(lim.slept, 0.1, places=6)
        clock.now = 10.0
        lim.consume(100)
        self.assertAlmostEqual(lim.slept, 0.1, places=6)


class Concurrency(SyncCase):
    def test_second_run_is_refused_while_lock_held(self):
        self.make_date('2026-11-24', {'a.bin': b'a'})
        lock = self.state / ('lock-oracle46-nas240.lock')
        lock.write_text('busy', encoding='utf-8')
        r = self.run_cfg(self.config())
        self.assertEqual(r['status'], 'errors')
        self.assertIn('locked', ''.join(r['results'][0]['errors']))
        self.assertTrue(lock.exists())
        self.assertEqual(self.target_dates(), [])


class ConfigGuards(unittest.TestCase):
    def base(self, **over):
        job = {'name': 'j', 'asset': 'a', 'source_root': 'C:/src/root', 'rate_limit_bytes_per_sec': 10,
               'targets': [{'name': 't', 'root': 'C:/dst/root'}]}
        job.update(over)
        return {'schema_version': 1, 'state_dir': 'C:/state', 'jobs': [job]}

    def test_valid_config_normalizes_and_orders_by_priority(self):
        cfg = self.base()
        cfg['jobs'].append({'name': 'first', 'priority': 0, 'asset': 'b', 'source_root': 'C:/s2',
                            'rate_limit_bytes_per_sec': 5, 'targets': [{'name': 'x', 'root': 'C:/d2'}]})
        out = sync.validate_config(cfg)
        self.assertEqual([j['name'] for j in out['jobs']], ['first', 'j'])
        self.assertEqual(out['jobs'][1]['receipt_name'], 'complete.json')

    def test_placeholders_are_refused(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(source_root='CHANGE_ME\\share'))

    def test_overlapping_target_and_source_refused(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(targets=[{'name': 't', 'root': 'C:/src/root/inside'}]))

    def test_duplicate_target_names_refused(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(targets=[{'name': 't', 'root': 'C:/d1'}, {'name': 't', 'root': 'C:/d2'}]))

    def test_non_positive_rate_refused(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(rate_limit_bytes_per_sec=0))

    def test_unsafe_asset_and_receipt_names_refused(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(asset='..'))
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(self.base(receipt_name='../complete.json'))

    def test_wrong_schema_refused(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config({'schema_version': 2, 'state_dir': 'x', 'jobs': []})


class ExampleConfigs(unittest.TestCase):
    NAMES = ['oracle46-nas243-to-nas240.example.json', 'svn29.example.json',
             'crm200.example.json', 'kingdee252.example.json']

    def test_four_redacted_examples_exist_and_are_placeholder_only(self):
        d = ROOT / 'platform' / 'sync' / 'examples'
        for name in self.NAMES:
            p = d / name
            self.assertTrue(p.exists(), name)
            text = p.read_text(encoding='utf-8')
            self.assertIn('CHANGE_ME', text, name)
            with self.assertRaises(sync.ConfigError):
                sync.load_config(str(p))

    def test_examples_become_valid_when_placeholders_replaced(self):
        d = ROOT / 'platform' / 'sync' / 'examples'
        for name in self.NAMES:
            text = (d / name).read_text(encoding='utf-8')
            parts = text.split('CHANGE_ME')
            fixed = parts[0] + ''.join('C:/fixture/%d%s' % (i, p) for i, p in enumerate(parts[1:], 1))
            data = json.loads(fixed)
            cfg = sync.validate_config(data)
            self.assertTrue(cfg['jobs'])
            for job in cfg['jobs']:
                self.assertTrue(job['targets'])


class ResultAndCli(SyncCase):
    def test_result_written_atomically_and_exit_codes(self):
        self.make_date('2026-11-24', {'a.bin': b'a'})
        cfg = self.config()
        cfg_path = self.base / 'cfg.json'
        cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
        out = self.base / 'result.json'
        with contextlib.redirect_stdout(io.StringIO()):
            rc = sync.main(['--config', str(cfg_path), '--result', str(out)])
        self.assertEqual(rc, 0)
        data = json.loads(out.read_text('utf-8'))
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['results'][0]['copied'], ['2026-11-24'])
        self.assertFalse([p for p in self.base.iterdir() if p.name.endswith('.tmp')])

    def test_config_error_exit_code_2(self):
        bad = self.base / 'bad.json'
        bad.write_text('{"schema_version": 9}', encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sync.main(['--config', str(bad)]), 2)

    def test_errors_exit_code_1(self):
        self.make_date('2026-11-24', {'a.bin': b'a'}, owner='wrong')
        cfg_path = self.base / 'cfg.json'
        cfg_path.write_text(json.dumps(self.config()), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync.main(['--config', str(cfg_path), '--result', str(self.base / 'r.json')]), 1)


if __name__ == '__main__':
    unittest.main()
