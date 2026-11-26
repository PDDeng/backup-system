"""UI-04 regression tests: strict receipt payload check, unknown-not-zero history, and visible extras failures."""
import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import export_feed
from feed_extras import _dir_has_valid_receipt, build_history_7d

BJ = dt.timezone(dt.timedelta(hours=8))


def write_receipt(folder: Path, body: dict, name: str = "backup-result.json"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(body), encoding="utf-8")


class StrictReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pending_only_directory_without_payload_is_not_valid(self):
        d = self.root / "2026-11-25"
        write_receipt(d, {"owner": "core-distributor-v1", "files": {"a.bin": {"size": 3}}}, "pending.json")
        self.assertFalse(_dir_has_valid_receipt(d))

    def test_receipt_with_matching_payload_is_valid_in_dict_list_and_pascal_forms(self):
        d = self.root / "2026-11-25"
        d.mkdir()
        (d / "a.bin").write_bytes(b"abc")
        write_receipt(d, {"files": {"a.bin": {"size": 3}}})
        self.assertTrue(_dir_has_valid_receipt(d))
        write_receipt(d, {"files": [{"name": "a.bin", "bytes": 3}]})
        self.assertTrue(_dir_has_valid_receipt(d))
        write_receipt(d, {"Status": "hotcopy_verified", "Files": [{"Name": "a.bin", "Bytes": 3}]}, "hotcopy-result.json")
        self.assertTrue(_dir_has_valid_receipt(d))

    def test_payload_size_mismatch_or_missing_is_not_valid(self):
        d = self.root / "2026-11-25"
        d.mkdir()
        (d / "a.bin").write_bytes(b"ab")
        write_receipt(d, {"files": {"a.bin": {"size": 3}}})
        self.assertFalse(_dir_has_valid_receipt(d))
        write_receipt(d, {"files": {"missing.bin": {"size": 3}}})
        self.assertFalse(_dir_has_valid_receipt(d))

    def test_traversal_and_absolute_payload_names_are_rejected(self):
        d = self.root / "2026-11-25"
        d.mkdir()
        (d / "a.bin").write_bytes(b"abc")
        for bad in ("../a.bin", "..\\a.bin", "/a.bin", "C:a.bin", "x/../a.bin"):
            write_receipt(d, {"files": {bad: {"size": 3}}})
            self.assertFalse(_dir_has_valid_receipt(d), bad)

    def test_failed_and_smalltest_receipts_stay_invalid(self):
        d = self.root / "2026-11-25"
        d.mkdir()
        (d / "a.bin").write_bytes(b"abc")
        write_receipt(d, {"Status": "failed", "files": {"a.bin": {"size": 3}}})
        self.assertFalse(_dir_has_valid_receipt(d))
        write_receipt(d, {"SmallTest": True, "files": {"a.bin": {"size": 3}}})
        self.assertFalse(_dir_has_valid_receipt(d))


def snapshot_at(now: dt.datetime, sampled: dt.datetime, assets=None, targets=None):
    return {"sampled": sampled.isoformat(), "targets": targets or [], "assets": assets or []}


class HistoryUnknownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = dt.datetime(2026, 11, 25, 12, 0, tzinfo=BJ)

    def tearDown(self):
        self.tmp.cleanup()

    def project_stub(self, summary):
        return {"summary": summary, "stale": False}

    def test_stale_snapshot_makes_today_unknown_not_live_100_percent(self):
        snap = snapshot_at(self.now, self.now - dt.timedelta(hours=3))
        hist = build_history_7d(snap, self.project_stub({"total": 14, "delivered": 14, "attention": 0}), now=self.now)
        today = hist["days"][-1]
        self.assertEqual(today["source"], "stale_snapshot")
        self.assertIsNone(today["delivered"])
        self.assertIsNone(today["rate"])

    def test_future_snapshot_is_not_trusted(self):
        snap = snapshot_at(self.now, self.now + dt.timedelta(hours=2))
        hist = build_history_7d(snap, self.project_stub({"total": 14, "delivered": 14, "attention": 0}), now=self.now)
        self.assertEqual(hist["days"][-1]["source"], "stale_snapshot")

    def test_projection_flagged_stale_is_not_trusted(self):
        snap = snapshot_at(self.now, self.now)
        proj = {"summary": {"total": 14, "delivered": 14, "attention": 0}, "stale": True}
        hist = build_history_7d(snap, proj, now=self.now)
        self.assertEqual(hist["days"][-1]["source"], "stale_snapshot")

    def test_fresh_snapshot_keeps_live_today(self):
        snap = snapshot_at(self.now, self.now)
        hist = build_history_7d(snap, self.project_stub({"total": 14, "delivered": 12, "attention": 2}), now=self.now)
        today = hist["days"][-1]
        self.assertEqual(today["source"], "live_snapshot")
        self.assertEqual(today["delivered"], 12)

    def test_days_before_earliest_evidence_are_unknown_not_zero(self):
        mount = self.root / "core243"
        (mount / "asset_a" / "2026-11-24" / "R1").mkdir(parents=True)
        (mount / "asset_a" / "2026-11-24" / "R1" / "d.bin").write_bytes(b"abc")
        write_receipt(mount / "asset_a" / "2026-11-24" / "R1", {"files": {"d.bin": {"size": 3}}})
        snap = snapshot_at(self.now, self.now, targets=[{"id": "nas243", "path": str(mount), "status": "mounted"}],
                           assets=[{"id": "a", "asset": "asset_a", "type": "GitLab", "targets": ["nas243"], "copies": []}])
        hist = build_history_7d(snap, self.project_stub({"total": 1, "delivered": 0, "attention": 1}), now=self.now)
        by_date = {d["date"]: d for d in hist["days"]}
        self.assertEqual(by_date["2026-11-19"]["source"], "before_evidence")
        self.assertIsNone(by_date["2026-11-19"]["delivered"])
        self.assertIsNone(by_date["2026-11-19"]["rate"])
        self.assertEqual(by_date["2026-11-24"]["source"], "nas_receipts")
        self.assertEqual(by_date["2026-11-24"]["delivered"], 1)


class ExtrasFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_extras_failure_is_recorded_and_stale_files_removed(self):
        (self.root / "history-7d.json").write_text('{"old": true}', encoding="utf-8")
        (self.root / "smb-browser.json").write_text('{"old": true}', encoding="utf-8")
        snap = {"sampled": dt.datetime.now(dt.timezone.utc).isoformat(), "targets": "not-a-list", "assets": []}
        error = export_feed.write_extras(self.root, snap)
        self.assertIsNotNone(error)
        status = json.loads((self.root / "extras-status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "error")
        self.assertIn(error, status["error"])
        self.assertFalse((self.root / "history-7d.json").exists())
        self.assertFalse((self.root / "smb-browser.json").exists())

    def test_extras_success_writes_both_feeds_and_ok_status(self):
        snap = {"sampled": dt.datetime.now(dt.timezone.utc).isoformat(), "targets": [], "assets": []}
        self.assertIsNone(export_feed.write_extras(self.root, snap))
        self.assertTrue((self.root / "history-7d.json").exists())
        self.assertTrue((self.root / "smb-browser.json").exists())
        self.assertEqual(json.loads((self.root / "extras-status.json").read_text(encoding="utf-8"))["status"], "ok")

    def test_main_exits_nonzero_when_extras_fail(self):
        with mock.patch.object(export_feed, "export", return_value="RuntimeError: boom"):
            err = io.StringIO()
            with redirect_stderr(err):
                rc = export_feed.main([])
        self.assertEqual(rc, 1)
        self.assertIn("boom", err.getvalue())

    def test_main_exits_zero_when_everything_exports(self):
        with mock.patch.object(export_feed, "export", return_value=None):
            self.assertEqual(export_feed.main([]), 0)


class WebhookSummaryNoMd5Tests(unittest.TestCase):
    def test_build_summary_text_has_no_md5_and_lists_failure_details(self):
        import webhook_settings
        board = {
            "summary": {"total": 3, "delivered": 2, "attention": 1, "pending_verification": 1},
            "assets": [
                {"id": "185", "name": "GitLab 185", "state": "protected", "issues": []},
                {"id": "247", "name": "GitLab 247", "state": "pending_verification", "issues": []},
                {"id": "nexus51", "name": "Nexus 51", "state": "attention", "issues": ["nas243：缺少有效完成记录"]},
            ],
        }
        txt = webhook_settings.build_summary_text(board, test_prefix=True)
        self.assertIn("需备份数量：3 项", txt)
        self.assertIn("已备份数量：2 项（66.7%）", txt)
        self.assertIn("失败数量：1 项", txt)
        self.assertIn("- Nexus 51：nas243：缺少有效完成记录", txt)
        self.assertNotIn("MD5", txt)
        self.assertNotIn("md5", txt)


if __name__ == "__main__":
    unittest.main()
