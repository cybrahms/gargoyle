"""Command-line entry point with discoverable subcommands."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import __version__
from .options import add_read_options


def build_parser():
    from .query import add_arguments as query_arguments
    from .receiver import add_arguments as receive_arguments

    parser = argparse.ArgumentParser(
        prog="scope",
        description="Capture, inspect and query retained OTLP traces.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser(
        "view", help="open the interactive trace inspector", allow_abbrev=False
    )
    add_read_options(view)
    view.add_argument(
        "--demo",
        action="store_true",
        help="inspect synthetic traces; cannot be combined with input, enrichment or filters",
    )
    receive = commands.add_parser(
        "receive",
        help="receive OTLP/HTTP traces and write JSONL files",
        allow_abbrev=False,
    )
    receive_arguments(receive)
    query = commands.add_parser(
        "query",
        help="read and filter retained traces; print JSON to stdout",
        allow_abbrev=False,
    )
    query_arguments(query)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv or ["view"])
    if args.command == "receive":
        from .receiver import run

        run(args)
        return
    if args.command == "query":
        from .query import run

        run(args)
        return
    if args.demo and any(
        flag in {option.split("=", 1)[0] for option in argv}
        for flag in ("--input", "--crush-workers-dir", "--trace-id", "--since", "--all")
    ):
        parser.error(
            "--demo cannot be combined with input, enrichment or trace/time filters"
        )
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(
            2,
            "scope view needs an interactive terminal; use scope query for JSON output.\n",
        )
    from .store import Store
    from .tui import Inspector

    if args.demo:
        from .demo import export

        with tempfile.TemporaryDirectory(prefix="scope-demo-") as directory:
            source = Path(directory) / "demo.json"
            source.write_text(json.dumps(export()))
            Inspector(Store(source)).run()
    else:
        app = Inspector(Store(args.input, args.crush_workers_dir), args.trace_id)
        app.hours = 0 if args.all else args.since
        app.run()
