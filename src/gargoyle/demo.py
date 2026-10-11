"""Synthetic OTLP fixture generator; never reads local telemetry."""

import time


def export():
    now = time.time_ns()
    resources = []
    for trace_index, service in enumerate(("build-worker", "review-worker", "api"), 1):
        trace = f"{trace_index:032x}"
        base = now - trace_index * 120_000_000_000
        operations = (
            "session",
            "fetch sources",
            "resolve dependencies",
            "compile",
            "unit tests",
            "integration tests",
            "publish report",
            "notify",
        )
        spans = []
        for index, name in enumerate(operations):
            start = base + (0 if index == 0 else index * 5_000_000_000)
            end = (
                base + 45_000_000_000
                if index == 0
                else start + (index % 3 + 1) * 1_000_000_000
            )
            spans.append(
                {
                    "traceId": trace,
                    "spanId": f"{index + 1:016x}",
                    "parentSpanId": "" if index == 0 else "0000000000000001",
                    "name": name,
                    "startTimeUnixNano": str(start),
                    "endTimeUnixNano": str(end),
                    "status": {"code": 2 if trace_index == 2 and index == 5 else 0},
                    "attributes": [],
                }
            )
        resources.append(
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service}}
                    ]
                },
                "scopeSpans": [{"scope": {"name": "gargoyle.demo"}, "spans": spans}],
            }
        )
    return {"resourceSpans": resources}
