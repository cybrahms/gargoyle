"""Small OTLP/HTTP trace receiver with bounded bodies and rotating JSONL output."""

import base64
import gzip
import io
import json
import os
import shutil
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from google.protobuf.message import DecodeError
from google.rpc.status_pb2 import Status
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

_ID_FIELDS = {"traceId", "spanId", "parentSpanId"}


def convert_ids(value, to_hex):
    """OTLP JSON uses hex IDs, unlike protobuf's generic JSON byte mapping."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _ID_FIELDS and isinstance(item, str):
                value[key] = (
                    base64.b64decode(item).hex()
                    if to_hex
                    else base64.b64encode(bytes.fromhex(item)).decode()
                )
            else:
                convert_ids(item, to_hex)
    elif isinstance(value, list):
        for item in value:
            convert_ids(item, to_hex)
    return value


class Writer:
    def __init__(self, directory, max_file=64 << 20, min_free=256 << 20):
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_file = max_file
        self.min_free = min_free
        self.lock = threading.Lock()

    def append(self, body):
        line = (json.dumps(body, separators=(",", ":")) + "\n").encode()
        if len(line) > self.max_file:
            raise ValueError("Export exceeds the file size limit")
        with self.lock:
            if shutil.disk_usage(self.directory).free < self.min_free + len(line):
                raise OSError("Insufficient free disk space")
            date = datetime.now(UTC).strftime("%Y-%m-%d")
            index = 0
            while True:
                path = self.directory / f"traces-{date}.{index:06d}.jsonl"
                if (
                    not path.exists()
                    or path.stat().st_size + len(line) <= self.max_file
                ):
                    break
                index += 1
            with path.open("ab") as file:
                file.write(line)
                file.flush()
                os.fsync(file.fileno())


def make_server(
    directory,
    host="127.0.0.1",
    port=4318,
    *,
    max_body=16 << 20,
    max_file=64 << 20,
    min_free=256 << 20,
):
    writer = Writer(directory, max_file, min_free)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_args):
            pass

        def reply(self, code, body=b"{}", content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(body)

        def failure(self, http_code, rpc_code, message, binary=False):
            status = Status(code=rpc_code, message=message)
            if binary:
                self.reply(
                    http_code, status.SerializeToString(), "application/x-protobuf"
                )
            else:
                self.reply(http_code, json.dumps(MessageToDict(status)).encode())

        def do_GET(self):
            if self.path == "/healthz":
                self.reply(200, b'{"status":"ok"}')
            else:
                self.failure(404, 5, "Not found")

        def do_POST(self):
            content_type = (
                self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            )
            binary = content_type == "application/x-protobuf"
            if self.path != "/v1/traces":
                self.failure(404, 5, "Only /v1/traces is supported", binary)
                return
            if content_type not in ("application/json", "application/x-protobuf"):
                self.failure(415, 3, "Use application/json or application/x-protobuf")
                return
            if self.headers.get("Transfer-Encoding"):
                self.failure(411, 3, "A Content-Length is required", binary)
                return
            try:
                length = int(self.headers["Content-Length"])
                if length < 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                self.failure(400, 3, "Invalid or missing Content-Length", binary)
                return
            if length > max_body:
                self.failure(413, 8, "Request body too large", binary)
                return
            encoding = self.headers.get("Content-Encoding", "identity").lower()
            if encoding not in ("identity", "gzip"):
                self.failure(415, 3, "Unsupported Content-Encoding", binary)
                return
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete request body")
                if encoding == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as file:
                        raw = file.read(max_body + 1)
                if len(raw) > max_body:
                    self.failure(413, 8, "Decoded body too large", binary)
                    return
                request = ExportTraceServiceRequest()
                if binary:
                    request.ParseFromString(raw)
                else:
                    ParseDict(
                        convert_ids(json.loads(raw), False),
                        request,
                        ignore_unknown_fields=True,
                    )
                body = convert_ids(
                    MessageToDict(request, use_integers_for_enums=True), True
                )
            except (
                ValueError,
                TypeError,
                AttributeError,
                OSError,
                EOFError,
                DecodeError,
                ParseError,
            ) as exc:
                self.failure(
                    400, 3, f"Invalid OTLP export: {type(exc).__name__}", binary
                )
                return
            try:
                writer.append(body)
            except ValueError:
                self.failure(413, 8, "Export exceeds the file size limit", binary)
                return
            except OSError:
                self.failure(503, 14, "Trace persistence unavailable", binary)
                return
            # Full success leaves partialSuccess unset. Empty protobuf and {} JSON.
            self.reply(200, b"" if binary else b"{}", content_type)

    return ThreadingHTTPServer((host, port), Handler)


def add_arguments(parser):
    from .options import data_directory

    parser.add_argument(
        "--output",
        type=Path,
        default=data_directory(),
        metavar="DIRECTORY",
        help="directory to write received OTLP traces as JSONL; default: GARGOYLE_DATA_DIR or ~/.local/state/gargoyle",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="address to bind the HTTP listener (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=4318,
        help="HTTP listener port; 0 chooses an available port (default: 4318)",
    )
    parser.add_argument(
        "--max-body-mib",
        type=int,
        default=16,
        help="maximum request size before and after gzip decoding (default: 16 MiB)",
    )
    parser.add_argument(
        "--max-file-mib",
        type=int,
        default=64,
        help="rotate before a JSONL file exceeds this size (default: 64 MiB)",
    )
    parser.add_argument(
        "--min-free-mib",
        type=int,
        default=256,
        help="disk space to leave free; reject writes below this reserve (default: 256 MiB)",
    )


def run(args):
    if (
        args.max_body_mib <= 0
        or args.max_file_mib <= 0
        or args.min_free_mib < 0
        or not 0 <= args.port <= 65535
    ):
        raise SystemExit(
            "body/file limits must be positive; disk reserve nonnegative; port between 0 and 65535"
        )
    with make_server(
        args.output,
        args.host,
        args.port,
        max_body=args.max_body_mib << 20,
        max_file=args.max_file_mib << 20,
        min_free=args.min_free_mib << 20,
    ) as server:
        print(
            f"OTLP traces: http://{args.host}:{server.server_port}/v1/traces → {args.output}",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
