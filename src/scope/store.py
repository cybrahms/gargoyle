"""Cached reader for raw OTLP exports and sink-wrapped JSONL records."""

import gzip
import json
import threading
from pathlib import Path

from .context import enrich


class Store:
    def __init__(self, directory, workers_root=None):
        self.directory = Path(directory).expanduser()
        self.workers_root = workers_root
        self.cache = {}
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            paths = (
                [self.directory]
                if self.directory.is_file()
                else sorted(
                    set(self.directory.glob("*.jsonl*"))
                    | set(self.directory.glob("*.json*"))
                )
            )
            warnings = []
            live = set()
            for p in paths:
                if not p.name.endswith((".jsonl", ".jsonl.gz", ".json", ".json.gz")):
                    continue
                live.add(str(p))
                try:
                    st = p.stat()
                    signature = (st.st_ino, st.st_size, st.st_mtime_ns)
                    if str(p) in self.cache and self.cache[str(p)][0] == signature:
                        continue
                    records = []
                    opener = gzip.open if p.name.endswith(".gz") else open
                    with opener(p, "rt") as f:
                        lines = (
                            [f.read()] if p.name.endswith((".json", ".json.gz")) else f
                        )
                        for line in lines:
                            try:
                                rec = json.loads(line)
                                body = rec.get("body", rec)
                                for rs in body.get("resourceSpans", []):
                                    resource = attrs(
                                        rs.get("resource", {}).get("attributes", [])
                                    )
                                    for scope in rs.get("scopeSpans", []):
                                        for s in scope.get("spans", []):
                                            a = {
                                                **resource,
                                                **attrs(s.get("attributes", [])),
                                            }
                                            start = int(s["startTimeUnixNano"]) / 1e6
                                            end = int(s["endTimeUnixNano"]) / 1e6
                                            records.append(
                                                {
                                                    "trace": s["traceId"],
                                                    "id": s["spanId"],
                                                    "parent": s.get("parentSpanId", ""),
                                                    "session": a.get(
                                                        "agent.session.id", ""
                                                    ),
                                                    "name": a.get("agent.tool.name")
                                                    or s.get("name", "span"),
                                                    "start": start,
                                                    "end": end,
                                                    "error": s.get("status", {}).get(
                                                        "code"
                                                    )
                                                    in (2, "STATUS_CODE_ERROR"),
                                                    "service": a.get(
                                                        "service.name", "unknown"
                                                    ),
                                                    "worker": a.get("fleet.worker")
                                                    or a.get("harness.name")
                                                    or a.get("service.name")
                                                    or "unknown",
                                                }
                                            )
                            except (ValueError, TypeError, KeyError, AttributeError):
                                # An active file can end with an unfinished export; retry on its next change.
                                continue
                    self.cache[str(p)] = (signature, records)
                except (OSError, EOFError) as e:
                    warnings.append(
                        f"{p.name}: {type(e).__name__}; retrying next refresh"
                    )
            self.cache = {p: v for p, v in self.cache.items() if p in live}
            spans = {}
            for _, records in self.cache.values():
                for s in records:
                    spans[(s["trace"], s["id"])] = s
            output = [dict(s) for s in spans.values()]
            enrich(output, self.workers_root)
            return {
                "spans": output,
                "files": len(live),
                "warnings": warnings,
                "source": str(self.directory),
                "missingDirectory": not self.directory.exists(),
            }


def attrs(items):
    result = {}
    for a in items:
        v = a.get("value", {})
        for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if key in v:
                result[a["key"]] = v[key]
                break
    return result
