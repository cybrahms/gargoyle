"""Shared, explicit command-line input and filtering options."""

import os
import re
from pathlib import Path


def data_directory():
    return Path(
        os.environ.get("GARGOYLE_DATA_DIR", "~/.local/state/gargoyle")
    ).expanduser()


def lookback(value):
    from argparse import ArgumentTypeError

    match = re.fullmatch(r"(\d+(?:\.\d+)?)(m|h|d)", value)
    if not match or float(match[1]) <= 0:
        raise ArgumentTypeError(
            "use a positive duration such as 30m, 1h or 7d; use --all for all retained data"
        )
    return float(match[1]) * {"m": 1 / 60, "h": 1, "d": 24}[match[2]]


def add_read_options(parser):
    parser.add_argument(
        "--input",
        type=Path,
        default=data_directory(),
        metavar="PATH",
        help="read OTLP JSON/JSONL from a file or directory (also gzip); default: GARGOYLE_DATA_DIR or ~/.local/state/gargoyle",
    )
    parser.add_argument(
        "--crush-workers-dir",
        type=Path,
        metavar="DIRECTORY",
        help="optional read-only enrichment from DIRECTORY/<worker>/data/crush.db",
    )
    parser.add_argument("--trace-id", metavar="ID", help="select an exact trace ID")
    window = parser.add_mutually_exclusive_group()
    window.add_argument(
        "--since",
        type=lookback,
        default=24,
        metavar="DURATION",
        help="keep traces with a span ending within this lookback: 30m, 1h, 7d (default: 24h)",
    )
    window.add_argument(
        "--all",
        action="store_true",
        help="include all retained traces, regardless of time",
    )
