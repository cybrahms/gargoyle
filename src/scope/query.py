"""Filter retained traces from a shell using the inspector's query rules."""

import argparse
import json
import sys

from .options import add_read_options
from .store import Store


def add_arguments(parser):
    add_read_options(parser)
    parser.add_argument(
        "--search",
        default="",
        metavar="TEXT",
        help="case-insensitive substring in worker, service, operation or trace ID; retains the entire matching trace",
    )
    parser.add_argument(
        "--with-errors",
        action="store_true",
        help="keep whole traces containing at least one OTLP error-status span",
    )
    parser.add_argument(
        "--spans",
        action="store_true",
        help="output all spans in matching traces instead of trace summaries",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="scope query",
        description="Filter retained traces and print JSON to stdout.",
        allow_abbrev=False,
    )
    add_arguments(parser)
    run(parser.parse_args(argv))


def run(args):
    from .tui import traces

    data = Store(args.input, args.crush_workers_dir).read()
    if data["missingDirectory"] or data["warnings"]:
        for warning in data["warnings"] or [f"Input not found: {args.input}"]:
            print(warning, file=sys.stderr)
        return_code = 1
    else:
        return_code = 0
    rows = [
        t
        for t in traces(data["spans"], args.search, (0 if args.all else args.since))
        if (not args.trace_id or t["id"] == args.trace_id)
        and (not args.with_errors or t["errors"])
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
