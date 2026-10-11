"""Filter retained traces from a shell using the inspector's query rules."""

import argparse
import json
import os
import sys
from pathlib import Path

from .store import Store
from .tui import traces


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Query retained traces as JSON without opening the TUI."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(
            os.environ.get("OTLP_SINK_DIR", "~/.local/state/otlp-sink")
        ).expanduser(),
    )
    parser.add_argument(
        "--workers-root", type=Path, help="optional read-only Crush enrichment"
    )
    parser.add_argument(
        "--search",
        default="",
        help="case-insensitive substring in worker, service, operation or trace ID",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="latest span end within N hours; 0 means all retained traces",
    )
    parser.add_argument("--trace", help="exact trace ID")
    parser.add_argument(
        "--errors",
        action="store_true",
        help="keep traces with at least one OTLP error status",
    )
    parser.add_argument(
        "--spans",
        action="store_true",
        help="output individual spans instead of trace summaries",
    )
    args = parser.parse_args(argv)
    if args.hours < 0:
        parser.error("--hours must be nonnegative")
    data = Store(args.source, args.workers_root).read()
    if data["missingDirectory"] or data["warnings"]:
        for warning in data["warnings"] or [f"Source not found: {args.source}"]:
            print(warning, file=sys.stderr)
        return_code = 1
    else:
        return_code = 0
    rows = [
        t
        for t in traces(data["spans"], args.search, args.hours)
        if (not args.trace or t["id"] == args.trace)
        and (not args.errors or t["errors"])
    ]
    output = (
        [s for t in rows for s in t["spans"]]
        if args.spans
        else [
            {
                **{k: v for k, v in t.items() if k != "spans"},
                "span_count": len(t["spans"]),
            }
            for t in rows
        ]
    )
    print(json.dumps(output, indent=2))
    if return_code:
        raise SystemExit(return_code)
