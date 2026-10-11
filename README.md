# Gargoyle

A live terminal inspector for retained OpenTelemetry traces. Browse traces,
inspect a timing waterfall, and copy span details. Capture exports with the
bundled OTLP/HTTP receiver, or point it at existing files.

Built with Textual. Charcoal panels, muted colors, keyboard navigation, and a
five-second refresh that preserves your selection and scroll position.

![Gargoyle displaying synthetic traces](docs/demo.svg)

## Install

Python 3.11 or newer:

```sh
uv tool install git+https://github.com/cybrahms/gargoyle.git
gargoyle --demo
```

Or install with pip in a virtual environment:

```sh
python -m venv .venv
.venv/bin/pip install git+https://github.com/cybrahms/gargoyle.git
.venv/bin/gargoyle --demo
```

The distribution is named `gargoyle-traces`; the command is `gargoyle`.
It is available from GitHub, not currently published to PyPI.

## Capture traces locally

Start the small bundled OTLP/HTTP server in one terminal:

```sh
gargoyle receive --source ./traces
```

It listens on `127.0.0.1:4318`, accepts `POST /v1/traces` as JSON or binary
protobuf (including gzip), and appends standard OTLP JSON to disk. In another
terminal, open the same directory:

```sh
gargoyle --source ./traces
```

Configure an instrumented application's OTLP/HTTP exporter:

```sh
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
# A signal-specific endpoint must include the path:
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318/v1/traces
```

Both HTTP encodings are supported. gRPC is not. These settings configure an
existing OpenTelemetry exporter; they do not instrument an application by
themselves. This receiver handles traces only: disable log/metric exporters or
send those signals elsewhere.

From a repository checkout, send a real trace using the Python SDK:

```sh
uv run --with opentelemetry-sdk --with opentelemetry-exporter-otlp-proto-http \
  python examples/send_trace.py
```

The script prints a trace ID and emits a parent `hello` span with a `do work`
child. Search `example-app` in the inspector, or select the printed trace ID.
For a dependency-free smoke test, send an OTLP JSON export with curl:

```sh
python3 - <<'PY' | curl --fail -sS http://127.0.0.1:4318/v1/traces \
  -H 'Content-Type: application/json' --data-binary @-
import json, time
now = time.time_ns()
print(json.dumps({"resourceSpans": [{
    "resource": {"attributes": [
        {"key": "service.name", "value": {"stringValue": "curl-example"}}]},
    "scopeSpans": [{"spans": [{
        "traceId": "0123456789abcdef0123456789abcdef",
        "spanId": "0123456789abcdef", "name": "hello from curl",
        "startTimeUnixNano": str(now - 1000000),
        "endTimeUnixNano": str(now)}]}]}]}))
PY
```

Success returns HTTP 200 with `{}` for JSON or an empty protobuf response.
Acknowledgment happens after the export is flushed and synced to disk.
`GET /healthz` checks that the HTTP process is responding; it does not test disk
writability. The wire format follows the
[OTLP/HTTP specification](https://opentelemetry.io/docs/specs/otlp/).

The receiver rotates at 64 MiB and at UTC day boundaries, checks for a 256 MiB
free-space reserve before writing, and bounds compressed and decoded request
bodies at 16 MiB. Change these with `--max-file-mib`, `--min-free-mib`, and
`--max-body-mib`. It returns 503 when persistence is unavailable and 413 for an
oversized export. Files are not automatically deleted or compressed; manage
retention yourself. Run one receiver per output directory. The default listener
is local; `--host` and `--port` change its address. It has no authentication or TLS.

## Inspect your exports

```sh
gargoyle --source /path/to/trace-exports
gargoyle --source /path/to/export.json
gargoyle --source /path/to/collector.jsonl.gz --trace <trace-id>
```

Directories are scanned for `.json`, `.jsonl`, and their gzip equivalents.
A `.json` file holds one export, optionally pretty printed; each `.jsonl` line
holds one export. Both standard OTLP/HTTP JSON and sink records wrapped as
`{"body": {"resourceSpans": [...]}}` are supported. Span fields use OTLP's
camelCase JSON encoding and Unix nanosecond timestamps.

The default source is `$OTLP_SINK_DIR`, falling back to
`~/.local/state/otlp-sink`. `--demo` generates synthetic examples and reads no
local telemetry. The inspector reads files independently of the receiver.
Existing Collector JSON file exports work as a source too.

## Query the data

In the TUI, `/` performs a case-insensitive substring search across worker,
service, operation name, and trace ID. `t` changes the time range; a trace matches
when its **latest span end**, not its start, falls within the range. Search keeps
all spans in a matching trace, so you can inspect surrounding work. It does not
search arbitrary attributes or enrichment text and is not a query language.

For scripts, use the same filters without an interactive terminal:

```sh
# Recent traces from a service/worker/operation:
gargoyle query --source ./traces --search example-app --hours 1

# All retained traces that recorded at least one OTLP error:
gargoyle query --source ./traces --hours 0 --errors

# Every span in one trace:
gargoyle query --source ./traces --hours 0 --trace <trace-id> --spans

# Combine with jq to count spans by operation:
gargoyle query --source ./traces --hours 0 --spans | \
  jq 'group_by(.name) | map({operation: .[0].name, count: length})'
```

Output is a JSON array of summaries (`id`, `worker`, `span_count`, `errors`,
`start`, `end`), or individual spans with `--spans`. Start/end timestamps are Unix
milliseconds. `--errors` retains entire traces containing errors, including their
other spans. `--workers-root` also enables enrichment in query output. Missing
sources/read failures go to stderr and produce a nonzero exit code; partial
readable results still appear on stdout.

## Navigation

| Key | Action |
| --- | --- |
| `h` / `l`, left / right | Move between Traces, Waterfall, and Details |
| `j` / `k`, up / down | Move through rows or scroll Details |
| `Tab` / `Shift+Tab` | Move focus, including the search field |
| `/` | Search worker, service, operation, or trace ID |
| Enter in search | Return to Traces |
| Enter on trace | Focus its waterfall |
| Enter on attributed span/task | Zoom into the reconstructed task |
| `a` | Show the whole session |
| `Esc` | Clear search and task zoom; leave search editing |
| `t` | Cycle last 24 hours, last hour, or all retained traces |
| `c` in Details | Copy the complete displayed record, including offscreen text |
| `r` | Refresh now |
| `p` | Pause/resume automatic refresh |
| `q` | Quit |

Trace lists use latest span end, so an old session with recent work remains
visible. Elapsed time includes gaps. Local timestamps use the machine's timezone.
Green waterfall bars are ordinary spans, rose bars have OTLP error status, and
blue bars contain `session` in their operation name. Indentation follows exported
parents. Services without worker attributes use their service name in the list.

Copy uses `pbcopy` when available, otherwise Textual's OSC 52 terminal clipboard
request. Your terminal must support clipboard requests for the fallback to work.
When running on a remote Mac, `pbcopy` copies to that Mac's clipboard.

## Optional Crush enrichment

```sh
gargoyle --source /path/to/exports --workers-root /path/to/workers
```

With this option, worker names from `fleet.worker` or `harness.name` select
`<workers-root>/<worker>/data/crush.db`. Databases are opened read-only. Without
this option Gargoyle does not access worker databases.

Tool calls match only when session, tool name, and exact start time identify one
call. Recorded message timestamps are assumed to use Unix seconds. Available
context includes selected command/path/title fields, tool result status, and
artifact URLs. Context is reread on refresh, so a later result can enrich an
existing span. Missing databases or unmatched calls leave context unavailable.

Successful `_claim` and matching `_complete` calls reconstruct task intervals.
Uncompleted claims remain open. Spans in exactly one interval receive a task;
overlapping intervals are ambiguous. Task headings are a presentation aid and
do not rewrite exported parent relationships. Selected text is bounded and
common credential assignments are redacted; this is limited pattern redaction.

## Run under harness

Copy [examples/harness.toml](examples/harness.toml) into your harness drop-in
directory and replace the source and command paths for your machine:

```sh
harness reload
harness start gargoyle
harness attach gargoyle
```

Use **Ctrl+b, then d** to detach while leaving the inspector running. `q` exits
the child and the example's restart policy starts it again; use `harness stop
gargoyle` to stop it deliberately. Each supervised instance has shared filters,
selection, and pause state for all attached users.

`NO_COLOR` disables the palette. If a supervisor inherits it unintentionally,
clear it in that child's launch command. A color-capable terminal works best;
wide terminals make more of the waterfall visible.

## Storage and limits

Files are cached by inode, size, and modification time, and changed files are
reread in full. Duplicate exports collapse by `(traceId, spanId)`. Partial JSONL
records are retried after the file changes; deleted files leave the cache and
gzip rotations are discovered on refresh. Read failures appear in the status
line. History is bounded by source retention.

This is a small local inspector: no pagination, indexed query engine,
independent retention, metrics/log view, or an indexed tracing backend. Large histories
will need bounded input or an indexed store.

## Development

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
uv build
uv run gargoyle --demo
uv run gargoyle receive --help
uv run gargoyle query --help
```

Tests cover append/partial records, rotation, deduplication, truncation,
optional enrichment and late results, navigation, clipboard payloads, task zoom,
resize, and refresh preservation with reordered traces and inserted spans. Receiver
tests cover JSON/protobuf/gzip round trips, rotating output, malformed requests,
body limits, and persistence failure acknowledgments.

MIT licensed.
