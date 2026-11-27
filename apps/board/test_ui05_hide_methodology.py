"""UI-05 regression tests: hide statistical methodology & provenance labels on backup board UI."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"


class _TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        classes = set((attr_map.get("class") or "").split())
        entry = {
            "tag": tag,
            "id": attr_map.get("id"),
            "classes": classes,
            "attrs": attr_map,
            "has_hidden": "hidden" in attr_map,
            "text": [],
        }
        self.elements.append(entry)
        self._stack.append(entry)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i]["tag"] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        for entry in self._stack:
            entry["text"].append(data)


def _extract_js_function(source: str, name: str) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source)
    if not match:
        raise AssertionError(f"Function {name!r} not found in app.js")
    idx = match.end() - 1
    depth = 0
    for pos in range(idx, len(source)):
        ch = source[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : pos + 1]
    raise AssertionError(f"Unterminated function {name!r} in app.js")


class HideMethodologyHtmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        parser = _TagCollector()
        parser.feed(cls.html)
        cls.elements = parser.elements

    def _by_id(self, el_id: str):
        matches = [e for e in self.elements if e["id"] == el_id]
        self.assertEqual(len(matches), 1, f"Expected exactly 1 element with id={el_id!r}, got {len(matches)}")
        return matches[0]

    def _by_class(self, cls_name: str):
        return [e for e in self.elements if cls_name in e["classes"]]

    def test_methodology_and_provenance_elements_are_hidden(self):
        for el_id in ("glossary-btn", "provenance-section", "history-provenance", "smb-provenance"):
            el = self._by_id(el_id)
            self.assertTrue(el["has_hidden"], f"#{el_id} must have hidden attribute")

        report_foots = self._by_class("report-foot")
        self.assertEqual(len(report_foots), 1)
        self.assertTrue(report_foots[0]["has_hidden"], ".report-foot must have hidden attribute")

        foot_sources = self._by_class("foot-source-tag")
        self.assertEqual(len(foot_sources), 1)
        self.assertTrue(foot_sources[0]["has_hidden"], ".foot-source-tag must have hidden attribute")

        panel_provs = self._by_class("panel-provenance")
        self.assertGreaterEqual(len(panel_provs), 4, "Expected provenance rows across storage/risk/history/smb panels")
        for el in panel_provs:
            self.assertTrue(el["has_hidden"], f".panel-provenance ({el['id'] or 'anonymous'}) must have hidden attribute")

    def test_daily_note_has_clean_push_schedule_text(self):
        daily_note = self._by_id("daily-note")
        text = "".join(daily_note["text"]).strip()
        self.assertEqual(text, "每天 08:50 自动推送至企业微信")
        self.assertNotIn("数据来源", text)
        self.assertNotIn("/api/board", text)

    def test_core_controls_and_strict_csp_preserved(self):
        for el_id in ("refresh", "export", "md5-btn", "passwd", "logout", "result-count", "kpis", "asset-table", "storage", "issues"):
            el = self._by_id(el_id)
            self.assertFalse(el["has_hidden"], f"Core element #{el_id} must remain visible")

        self.assertEqual(len(self._by_class("foot-guide")), 1)
        self.assertFalse(self._by_class("foot-guide")[0]["has_hidden"])

        for el in self.elements:
            self.assertNotEqual(el["tag"], "style", "Strict CSP forbids inline <style>")
            if el["tag"] == "script":
                self.assertIn("src", el["attrs"], "Strict CSP forbids inline <script>")
            for attr_name in el["attrs"]:
                self.assertNotEqual(attr_name, "style", f"Strict CSP forbids inline style on <{el['tag']}>")
                self.assertFalse(attr_name.startswith("on"), f"Strict CSP forbids inline event handler {attr_name}")


class HideMethodologyJsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_render_kpis_omits_metric_def_and_methodology_labels(self):
        body = _extract_js_function(self.js, "renderKpis")
        self.assertNotIn("metric-def", body)
        self.assertNotIn("口径:", body)
        self.assertNotIn("来源:", body)

    def test_masthead_sample_text_omits_api_board_source(self):
        body = _extract_js_function(self.js, "render")
        self.assertIn("$('sample').textContent = '证据采样 ' + formatSampled(data.sampled);", body)
        self.assertNotIn("来源 /api/board", body)

    def test_drawer_renders_omit_source_notes(self):
        result_body = _extract_js_function(self.js, "renderDrawerResult")
        restore_body = _extract_js_function(self.js, "renderDrawerRestore")
        for fn_name, body in (("renderDrawerResult", result_body), ("renderDrawerRestore", restore_body)):
            self.assertNotIn("source-note", body, f"{fn_name} must not emit .source-note")
            self.assertNotIn("来源：", body, f"{fn_name} must not emit 来源： label")

    def test_hidden_provenance_handlers_are_null_safe_and_never_unhide_smb_provenance(self):
        self.assertIn("if ($('glossary-btn'))", self.js)
        self.assertIn("if ($('foot-glossary'))", self.js)
        self.assertIn("if ($('foot-source'))", self.js)
        self.assertNotIn("$('smb-provenance').hidden = false", self.js)


if __name__ == "__main__":
    unittest.main()
