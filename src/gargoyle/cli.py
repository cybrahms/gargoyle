"""Command-line entry point."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from . import __version__
from .store import Store
from .tui import Inspector


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "receive":
        from .receiver import main as receive

        receive(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "query":
        from .query import main as query

        query(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(
        description="Live terminal inspector for retained OTLP traces. Also: gargoyle receive --help; gargoyle query --help."
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(
            os.environ.get("OTLP_SINK_DIR", "~/.local/state/otlp-sink")
        ).expanduser(),
        help="JSON/JSONL file or directory (also gzip); default: OTLP_SINK_DIR or ~/.local/state/otlp-sink",
    )
    parser.add_argument(
        "--workers-root",
        type=Path,
        help="optional Crush enrichment: <root>/<worker>/data/crush.db",
    )
    parser.add_argument("--trace", help="initial trace ID")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="show synthetic traces without reading local data",
    )
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(
            2, "gargoyle needs an interactive terminal (try gargoyle --help).\n"
        )
    if args.demo:
        from .demo import export

        with tempfile.TemporaryDirectory(prefix="gargoyle-demo-") as directory:
            source = Path(directory) / "demo.json"
            source.write_text(json.dumps(export()))
            Inspector(Store(source), args.trace).run()
    else:
        Inspector(Store(args.source, args.workers_root), args.trace).run()
