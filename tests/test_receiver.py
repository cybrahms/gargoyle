import gzip
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from google.protobuf.json_format import ParseDict
from google.rpc.status_pb2 import Status
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from gargoyle.demo import export
from gargoyle.receiver import convert_ids, make_server
from gargoyle.store import Store


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.server = make_server(
            self.root, port=0, max_body=10000, max_file=8000, min_free=0
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(
        self, body, content_type="application/json", encoding=None, path="/v1/traces"
    ):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        self.addCleanup(connection.close)
        headers = {"Content-Type": content_type}
        if encoding:
            headers["Content-Encoding"] = encoding
        connection.request("POST", path, body, headers)
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")

    def test_json_gzip_and_protobuf_persist_same_hex_ids_and_rotate(self):
        body = export()
        raw = json.dumps(body).encode()
        status, response, _ = self.request(raw)
        self.assertEqual((status, response), (200, b"{}"))
        self.assertEqual(self.request(gzip.compress(raw), encoding="gzip")[0], 200)
        message = ParseDict(convert_ids(body, False), ExportTraceServiceRequest())
        status, response, content_type = self.request(
            message.SerializeToString(), "application/x-protobuf"
        )
        self.assertEqual(
            (status, response, content_type), (200, b"", "application/x-protobuf")
        )
        paths = list(self.root.glob("*.jsonl"))
        self.assertGreater(len(paths), 1)
        spans = Store(self.root).read()["spans"]
        self.assertEqual(len(spans), 24)
        self.assertEqual(spans[0]["trace"], "00000000000000000000000000000001")
        self.assertTrue(any(s["error"] for s in spans))

    def test_malformed_unsupported_oversize_and_gzip_bomb_are_not_persisted(self):
        for raw, kind, encoding, expected in [
            (b"invalid", "application/json", None, 400),
            (b'{"resourceSpans":"invalid"}', "application/json", None, 400),
            (b"\xff", "application/x-protobuf", None, 400),
            (b"{}", "text/plain", None, 415),
            (b"{}", "application/json", "br", 415),
            (b"x" * 10001, "application/json", None, 413),
            (gzip.compress(b" " * 10001), "application/json", "gzip", 413),
        ]:
            with self.subTest(kind=kind, encoding=encoding, expected=expected):
                status, response, _ = self.request(raw, kind, encoding)
                self.assertEqual(status, expected)
                if kind == "application/x-protobuf":
                    self.assertEqual(Status.FromString(response).code, 3)
        self.assertEqual(list(self.root.glob("*.jsonl")), [])
        self.assertEqual(self.request(b"{}", path="/v1/logs")[0], 404)

    def test_failed_disk_write_is_not_acknowledged(self):
        with patch(
            "gargoyle.receiver.Writer.append", side_effect=OSError("disk unavailable")
        ):
            self.assertEqual(self.request(b"{}")[0], 503)
        self.assertEqual(list(self.root.glob("*.jsonl")), [])
