"""Unit tests for UI-03 / DATA-01: webhook_settings, real 7-day history trend, and SMB browser."""
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

from feed_extras import build_history_7d, build_smb_browser
from model import project
from webhook_settings import build_summary_text, mask_url, read_settings, save_settings, send_test_message, validate_webhook_url


class DummyResponse:
    def __init__(self, payload):
        self._buf = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._buf

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class WebhookSettingsTests(unittest.TestCase):
    def test_validate_and_mask_url(self):
        good = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcd1234-ef56-7890"
        self.assertEqual(validate_webhook_url(" " + good + " "), good)
        self.assertEqual(mask_url(good), "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=****7890")
        for bad in (
            "",
            "http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcd1234567890",
            "https://127.0.0.1/webhook?key=1234567890",
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=short",
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=bad key with space",
        ):
            with self.assertRaises(ValueError):
                validate_webhook_url(bad)

    def test_save_keeps_existing_key_on_blank_and_test_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "daily-webhook.json"
            init = read_settings(cfg)
            self.assertFalse(init["enabled"])
            self.assertFalse(init["has_url"])
            self.assertTrue(init["writable"])

            # Cannot enable without URL
            with self.assertRaises(ValueError):
                save_settings({"enabled": True}, cfg)

            url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=testkey-0123456789"
            saved = save_settings({"enabled": True, "webhook_url": url}, cfg)
            self.assertTrue(saved["enabled"])
            self.assertTrue(saved["has_url"])
            self.assertTrue(saved["masked_url"].endswith("****6789"))
            self.assertNotIn("testkey-0123456789", saved["masked_url"])

            # Toggle off without re-sending URL preserves URL on disk
            toggled = save_settings({"enabled": False, "webhook_url": ""}, cfg)
            self.assertFalse(toggled["enabled"])
            self.assertTrue(toggled["has_url"])
            self.assertTrue(toggled["masked_url"].endswith("****6789"))

            # Mock test send
            captured = {}

            def fake_open(req, timeout=10):
                captured["url"] = req.full_url
                captured["body"] = json.loads(req.data.decode("utf-8"))
                return DummyResponse({"errcode": 0, "errmsg": "ok"})

            board = {
                "summary": {"total": 2, "delivered": 1, "attention": 1, "pending_verification": 0},
                "assets": [
                    {"name": "GitLab 1", "state": "protected", "restore": {"status": "passed"}},
                    {"name": "Nexus 51", "state": "attention", "restore": {"status": "not_tested"}},
                ],
            }
            res = send_test_message(board, cfg, opener=fake_open)
            self.assertTrue(res["ok"])
            self.assertEqual(captured["url"], url)
            self.assertIn("Nexus 51", captured["body"]["text"]["content"])
            self.assertIn("1/2", captured["body"]["text"]["content"])
            after = read_settings(cfg)
            self.assertTrue(after["last_test"]["ok"])


class FeedExtrasTests(unittest.TestCase):
    def test_smb_browser_and_history_7d_real_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m243 = root / "core243"
            m240 = root / "core240"
            m243.mkdir()
            m240.mkdir()

            # Valid receipt on 2026-11-24 and 2026-11-25 for asset A; only .partial on 2026-11-25 for asset B
            a_d1 = m243 / "asset_a" / "2026-11-24" / "20261124T010000-1"
            a_d1.mkdir(parents=True)
            (a_d1 / "backup-result.json").write_text(json.dumps({"Status": "success", "Date": "2026-11-24"}), encoding="utf-8")

            a_d2 = m243 / "asset_a" / "2026-11-25" / "20261125T010000-1"
            a_d2.mkdir(parents=True)
            (a_d2 / "backup-result.json").write_text(json.dumps({"Status": "success", "Date": "2026-11-25"}), encoding="utf-8")

            b_partial = m240 / "asset_b" / "2026-11-25" / ".20261125T010000.partial"
            b_partial.mkdir(parents=True)
            (b_partial / "backup-result.json").write_text(json.dumps({"Status": "success"}), encoding="utf-8")

            b_small = m240 / "asset_b" / "2026-11-24" / "20261124T010000-2"
            b_small.mkdir(parents=True)
            (b_small / "hotcopy-result.json").write_text(json.dumps({"Status": "hotcopy_verified", "SmallTest": True}), encoding="utf-8")

            now = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
            targets = [
                {"id": "nas243", "path": str(m243), "status": "mounted", "total": 1000, "free": 600},
                {"id": "nas240", "path": str(m240), "status": "mounted", "total": 1000, "free": 500},
            ]
            browser = build_smb_browser(targets, now=now)
            self.assertEqual(len(browser["shares"]), 2)
            s243 = browser["shares"][0]
            self.assertTrue(s243["healthy"])
            self.assertEqual(s243["unc"], r"\\10.10.11.243\资源备份")
            self.assertEqual(s243["entries"][0]["name"], "asset_a")
            self.assertTrue(s243["entries"][0]["children"][0]["has_receipt"])

            s240 = browser["shares"][1]
            # Partial & SmallTest must NOT count as valid receipts
            self.assertFalse(any(c["has_receipt"] for c in s240["entries"][0]["children"]))

            snapshot = {
                "sampled": now.isoformat(),
                "targets": targets,
                "assets": [
                    {
                        "id": "a",
                        "name": "Asset A",
                        "type": "GitLab",
                        "asset": "asset_a",
                        "targets": ["nas243"],
                        "copies": [{"target": "nas243", "status": "receipt_found", "date": "2026-11-25"}],
                        "diagnosis": {"status": "ok"},
                    },
                    {
                        "id": "b",
                        "name": "Asset B",
                        "type": "SVN",
                        "asset": "asset_b",
                        "targets": ["nas240"],
                        "copies": [],
                        "diagnosis": {"status": "ok"},
                    },
                ],
            }
            projected = project(snapshot, now=now.astimezone(dt.timezone.utc))
            hist = build_history_7d(snapshot, projected, now=now)
            self.assertEqual(len(hist["days"]), 7)
            day25 = hist["days"][-1]
            day24 = hist["days"][-2]
            day20 = hist["days"][1]
            self.assertEqual(day25["date"], "2026-11-25")
            self.assertEqual(day25["source"], "live_snapshot")
            self.assertEqual(day25["rate"], 50.0)
            self.assertEqual(day24["date"], "2026-11-24")
            self.assertEqual(day24["source"], "nas_receipts")
            self.assertEqual(day24["delivered"], 1)
            self.assertEqual(day24["rate"], 50.0)
            # Earlier day before any receipts must show 0 delivered (not fabricated!)
            self.assertEqual(day20["delivered"], 0)
            self.assertEqual(day20["rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
