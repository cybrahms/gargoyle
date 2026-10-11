import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scope.demo import export
from scope.query import main


class QueryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "demo.json"
        self.source.write_text(json.dumps(export()))

    def query(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            main(["--input", str(self.source), *args])
        return json.loads(output.getvalue())

    def test_search_error_filter_and_span_output(self):
        rows = self.query("--search", "REVIEW", "--since", "1h", "--with-errors")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["worker"], "review-worker")
        self.assertEqual(rows[0]["span_count"], 8)
        spans = self.query("--trace-id", rows[0]["id"], "--spans", "--all")
        self.assertEqual(len(spans), 8)
        self.assertEqual(sum(s["error"] for s in spans), 1)
        self.assertEqual(self.query("--search", "not-found"), [])

    def test_latest_end_range(self):
        body = export()
        for rs in body["resourceSpans"]:
            for scope in rs["scopeSpans"]:
                for s in scope["spans"]:
                    s["startTimeUnixNano"] = "1000000000"
                    s["endTimeUnixNano"] = "2000000000"
        self.source.write_text(json.dumps(body))
        self.assertEqual(self.query(), [])
        self.assertEqual(len(self.query("--all")), 3)

    def test_missing_source_returns_failure_and_empty_array(self):
        self.source.unlink()
        output, errors = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(output),
            redirect_stderr(errors),
            self.assertRaises(SystemExit) as raised,
        ):
            main(["--input", str(self.source)])
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue()), [])
        self.assertIn("Input not found", errors.getvalue())
