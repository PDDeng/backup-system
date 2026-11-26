import unittest
from platform_core import convert, normalize
from daily_report import build_summary_message


class DailyReportSummaryTests(unittest.TestCase):
    def _snapshot(self):
        return {
            'sampled': '2026-11-26T08:50:00+08:00',
            'collecting': False,
            'targets': [],
            'assets': [
                # 1) GitLab with files_md5_rechecked transfer -> must count as available/success
                {
                    'id': '185', 'name': 'GitLab 10.10.10.185', 'type': 'GitLab',
                    'targets': ['nas243', 'nas240', 'win254'],
                    'execution': {'run': '20261126T010000-1', 'status': '分发记录齐全'},
                    'copies': [{'target': t, 'status': 'receipt_found', 'date': '2026-11-25', 'run': 'old'} for t in ('nas243', 'nas240', 'win254')],
                    'transfers': [{'target': t, 'status': 'files_md5_rechecked', 'date': '2026-11-26', 'run': '20261126T010000-1', 'verification': 'md5_verified_at 2026-11-26'} for t in ('nas243', 'nas240', 'win254')],
                },
                # 2) GitLab with files_size_rechecked (no MD5 yet) -> must ALSO count as success, no MD5 text
                {
                    'id': '247', 'name': 'GitLab 10.10.10.247', 'type': 'GitLab',
                    'targets': ['nas243', 'nas240', 'win254'],
                    'execution': {'run': '20261126T010000-2', 'status': '分发记录齐全'},
                    'copies': [],
                    'transfers': [{'target': t, 'status': 'files_size_rechecked', 'date': '2026-11-26', 'run': '20261126T010000-2', 'verification': 'pending_md5'} for t in ('nas243', 'nas240', 'win254')],
                },
                # 3) Asset with failed execution -> must count as failed even if old copy exists
                {
                    'id': 'nexus51', 'name': 'Nexus 10.10.10.51', 'type': 'Nexus',
                    'targets': ['nas243'],
                    'execution': {'status': 'failed'},
                    'copies': [{'target': 'nas243', 'status': 'receipt_found', 'date': '2026-11-26'}],
                },
                # 4) Asset with no valid result -> failed
                {
                    'id': 'svn29', 'name': 'SVN 192.168.0.29', 'type': 'SVN',
                    'targets': ['nas240'],
                    'copies': [{'target': 'nas240', 'status': 'receipt_found', 'date': '2026-11-19'}],
                },
                # 5) Asset with collection error -> clearly flagged as collection unknown
                {
                    'id': 'crm200', 'name': 'CRM 192.168.0.200', 'type': 'CRM',
                    'targets': ['nas240'],
                    'copies': [{'target': 'nas240', 'status': 'error', 'error': 'timeout'}],
                },
            ],
        }

    def test_md5_rechecked_transfer_is_available_in_platform_core(self):
        snap = self._snapshot()
        r = normalize(snap['assets'][0], snap['sampled'])
        self.assertEqual(r['coverage'], 'available')

    def test_summary_counts_pending_md5_as_success_and_hides_md5(self):
        data = convert(self._snapshot())
        msg = build_summary_message(data, '2026-11-26')
        self.assertIn('需备份数量：5 项', msg)
        self.assertIn('已备份数量：2 项（40.0%）', msg)
        self.assertIn('失败数量：3 项（含采集待确认 1 项）', msg)
        self.assertIn('- Nexus 10.10.10.51：备份任务执行报错', msg)
        self.assertIn('- SVN 192.168.0.29：备份记录已过期', msg)
        self.assertIn('- CRM 192.168.0.200：采集异常待确认', msg)
        self.assertNotIn('MD5', msg)
        self.assertNotIn('md5', msg)

    def test_all_success_shows_none_in_failure_details(self):
        snap = self._snapshot()
        snap['assets'] = snap['assets'][:2]
        data = convert(snap)
        msg = build_summary_message(data, '2026-11-26')
        self.assertIn('需备份数量：2 项', msg)
        self.assertIn('已备份数量：2 项（100.0%）', msg)
        self.assertIn('失败数量：0 项', msg)
        self.assertIn('失败明细：无', msg)
        self.assertNotIn('MD5', msg)


if __name__ == '__main__':
    unittest.main()
