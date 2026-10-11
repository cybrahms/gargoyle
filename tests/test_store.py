import gzip
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from gargoyle.demo import export
from gargoyle.store import Store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.body = export()

    def test_raw_wrapped_partial_append_rotation_dedup_truncation_and_deletion(self):
        path = self.root / "collector.jsonl"
        record = json.dumps(self.body)
        path.write_text(record + "\n" + '{"body":')
        store = Store(self.root)
        self.assertEqual(len(store.read()["spans"]), 24)
        with path.open("a") as file:
            file.write(record + "}\n")
        self.assertEqual(len(store.read()["spans"]), 24)
        rotated = self.root / "collector.jsonl.gz"
        with gzip.open(rotated, "wt") as file:
            file.write(json.dumps({"body": self.body}) + "\n")
        path.write_text("")
        self.assertEqual(len(store.read()["spans"]), 24)
        rotated.unlink()
        self.assertEqual(store.read()["spans"], [])

    def test_pretty_json_single_file_gzip_and_service_fallback(self):
        path = self.root / "export.json.gz"
        with gzip.open(path, "wt") as file:
            json.dump(self.body, file, indent=2)
        result = Store(path).read()
        self.assertEqual(len(result["spans"]), 24)
        self.assertEqual(result["spans"][0]["worker"], "build-worker")
        self.assertFalse(result["missingDirectory"])
        self.assertTrue(any(s["error"] for s in result["spans"]))

    def test_missing_source_and_unreadable_gzip(self):
        self.assertTrue(Store(self.root / "missing").read()["missingDirectory"])
        (self.root / "bad.jsonl.gz").write_bytes(b"not gzip")
        result = Store(self.root).read()
        self.assertEqual(result["spans"], [])
        self.assertTrue(result["warnings"])

    def test_optional_crush_enrichment_and_late_result(self):
        rs = self.body["resourceSpans"][0]
        rs["resource"]["attributes"].extend(
            [
                {"key": "fleet.worker", "value": {"stringValue": "test-worker"}},
                {"key": "agent.session.id", "value": {"stringValue": "test-session"}},
            ]
        )
        s = rs["scopeSpans"][0]["spans"][0]
        s["name"] = "bash"
        s["startTimeUnixNano"] = "1000000000"
        s["endTimeUnixNano"] = "2000000000"
        path = self.root / "export.json"
        path.write_text(json.dumps(self.body))
        workers = self.root / "workers"
        db = workers / "test-worker/data/crush.db"
        db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute(
                "CREATE TABLE messages (role TEXT, parts TEXT, created_at INTEGER, session_id TEXT)"
            )
            call = [
                {
                    "type": "tool_call",
                    "data": {
                        "id": "call-1",
                        "name": "bash",
                        "input": json.dumps({"command": "echo example"}),
                    },
                }
            ]
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?)",
                ("assistant", json.dumps(call), 1, "test-session"),
            )
        self.assertNotIn("context", Store(path).read()["spans"][0])
        store = Store(path, workers)
        self.assertEqual(store.read()["spans"][0]["context"]["Command"], "echo example")
        with closing(sqlite3.connect(db)) as conn, conn:
            result = [
                {
                    "type": "tool_result",
                    "data": {
                        "tool_call_id": "call-1",
                        "content": "{}",
                        "is_error": False,
                    },
                }
            ]
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?)",
                ("tool", json.dumps(result), 2, "test-session"),
            )
        self.assertEqual(
            store.read()["spans"][0]["context"]["Tool result"], "Returned successfully"
        )
