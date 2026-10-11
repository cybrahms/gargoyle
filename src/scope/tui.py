"""Live Textual inspector for retained station traces."""

import asyncio
import shutil
import subprocess
import time
from datetime import datetime
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Footer, Input, Static


def clean(value):
    return "".join(c if c.isprintable() else " " for c in str(value))


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def duration(ms):
    return f"{ms / 1000:.1f}s" if ms < 60000 else f"{ms / 60000:.1f}m"


def traces(spans, query="", hours=24, now=None):
    grouped = {}
    for span in spans:
        grouped.setdefault(span["trace"], []).append(span)
    cutoff = (time.time() if now is None else now) * 1000 - hours * 3600000
    rows = []
    for tid, ss in grouped.items():
        end = max(s["end"] for s in ss)
        if hours and end < cutoff:
            continue
        if (
            query.casefold()
            not in " ".join(
                str(s[k]) for s in ss for k in ("trace", "worker", "service", "name")
            ).casefold()
        ):
            continue
        rows.append(
            {
                "id": tid,
                "spans": ss,
                "start": min(s["start"] for s in ss),
                "end": end,
                "worker": ", ".join(sorted({s["worker"] for s in ss})),
                "errors": sum(s["error"] for s in ss),
            }
        )
    return sorted(rows, key=lambda t: (-t["end"], t["id"]))


def span_rows(ss, work=None):
    by_id = {s["id"]: s for s in ss}
    groups = {}
    for s in ss:
        g = s.get("work")
        key = (g["id"], g["start"]) if g else None
        if work is None or key == work:
            groups.setdefault(key, []).append(s)
    rows = []
    for key, members in sorted(
        groups.items(), key=lambda item: min(s["start"] for s in item[1])
    ):
        if key:
            rows.append(("group", members[0]["work"], key, 0))
        for s in sorted(members, key=lambda s: (s["start"], -s["end"], s["id"])):
            depth, parent, seen = 0, s["parent"], {s["id"]}
            while parent in by_id and parent not in seen:
                seen.add(parent)
                depth += 1
                parent = by_id[parent]["parent"]
            rows.append(("span", s, key, min(depth, 8)))
    return rows


class Details(VerticalScroll):
    can_focus = True


class Inspector(App):
    TITLE = "Scope / Trace Inspector"
    SUB_TITLE = "live OTLP inspection"
    CSS = """
    Screen { background: #18181b; color: #d4d4d8; }
    #brand { height: 3; padding: 1 2 0 2; }
    #brand-name { width: 1fr; color: #f4f4f5; text-style: bold; }
    #brand-hint { width: auto; color: #a1a1aa; }
    #search { margin: 1 2; height: 3; background: #27272a; border: none; padding: 1 2; }
    #search:focus { border-left: tall #a78bfa; }
    #panes { height: 1fr; margin: 0 2; }
    DataTable, Details { height: 1fr; border: none; border-top: solid #3f3f46; background: #202023; }
    DataTable:focus, Details:focus { border-top: solid #a78bfa; }
    #traces { width: 27%; }
    #waterfall { width: 40%; margin: 0 1; }
    #details { width: 1fr; padding: 1 2; }
    #detail-text { width: 1fr; }
    #status { height: 2; padding: 1 0 0 0; margin: 0 2; color: #a1a1aa; }
    DataTable > .datatable--header { background: #202023; color: #a1a1aa; text-style: none; }
    DataTable > .datatable--cursor { background: #3b3254; color: #fafafa; text-style: none; }
    DataTable > .datatable--even-row { background: #202023; }
    DataTable > .datatable--odd-row { background: #242427; }
    DataTable > .datatable--hover { background: #303034; }
    Footer { background: #27272a; color: #a1a1aa; }
    FooterKey { background: #27272a; }
    FooterKey > .footer-key--key { background: #27272a; color: #c4b5fd; text-style: none; }
    FooterKey > .footer-key--description { background: #27272a; color: #a1a1aa; }
    """
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
        ("slash", "search", "Search"),
        ("t", "range", "Range"),
        ("a", "session", "Session"),
        ("r", "refresh", "Refresh"),
        ("p", "pause", "Pause"),
        ("c", "copy", "Copy details"),
    ]

    def __init__(self, store, trace=None):
        super().__init__()
        self.store = store
        self.selected_trace = trace
        self.selected_row = None
        self.work = None
        self.hours = 24
        self.paused = False
        self.loading_data = False
        self.data = {"spans": [], "files": 0, "warnings": []}
        self.ts, self.rows = [], []
        self.details_text = "Select a trace to inspect its operations."

    def compose(self) -> ComposeResult:
        with Horizontal(id="brand"):
            yield Static("Trace inspector", id="brand-name")
            yield Static("Scope · live traces", id="brand-hint")
        yield Input(
            placeholder="/ Search worker, service, tool or trace ID", id="search"
        )
        with Horizontal(id="panes"):
            yield DataTable(id="traces", cursor_type="row", zebra_stripes=True)
            yield DataTable(id="waterfall", cursor_type="row", zebra_stripes=True)
            with Details(id="details"):
                yield Static(self.details_text, id="detail-text", markup=False)
        yield Static("Loading retained traces…", id="status", markup=False)
        yield Footer()

    def on_mount(self):
        self.theme = "textual-dark"
        table = self.query_one("#traces", DataTable)
        table.border_title = "Traces"
        table.add_column("Worker / activity / trace", width=32)
        waterfall = self.query_one("#waterfall", DataTable)
        waterfall.border_title = "Session waterfall"
        waterfall.add_column("Timing", width=12)
        waterfall.add_column("Operation / reconstructed task", width=42)
        waterfall.add_column("Duration", width=9)
        self.query_one("#details").border_title = "Details · c to copy"
        table.focus()
        self.set_interval(5, self.auto_refresh)
        self.action_refresh()

    def auto_refresh(self):
        if not self.paused:
            self.action_refresh()

    def action_refresh(self):
        if not self.loading_data:
            self.loading_data = True
            self.run_worker(self.read_data(), name="trace-refresh")

    async def read_data(self):
        try:
            self.data = await asyncio.to_thread(self.store.read)
            self.rebuild_traces()
            self.update_status()
        except Exception as exc:  # noqa: BLE001 — keep the inspector alive after refresh failures
            self.query_one("#status", Static).update(
                f"Refresh failed: {type(exc).__name__}: {exc}"
            )
        finally:
            self.loading_data = False

    def update_status(self):
        warnings = "; ".join(self.data.get("warnings", []))
        if self.data.get("missingDirectory"):
            warnings = "Source directory missing: " + self.data.get("source", "")
        range_name = f"{self.hours}h" if self.hours else "all retained"
        self.query_one("#status", Static).update(
            warnings
            or f"{len(self.ts)} traces · {len(self.data['spans'])} spans · {self.data['files']} files  │  {range_name}  │  "
            + (
                "PAUSED"
                if self.paused
                else "live · " + datetime.now().strftime("%H:%M:%S")
            )
            + "  │  h/l panes · j/k rows"
        )

    def rebuild_traces(self):
        table = self.query_one("#traces", DataTable)
        self.ts = traces(
            self.data["spans"], self.query_one("#search", Input).value, self.hours
        )
        ids = [t["id"] for t in self.ts]
        index = ids.index(self.selected_trace) if self.selected_trace in ids else 0
        if self.selected_trace not in ids:
            self.work, self.selected_row = None, None
        entries = []
        for t in self.ts:
            name = Text(clean(t["worker"]), style="bold #e4e4e7")
            name.append(f"  {len(t['spans'])} spans", style="dim")
            if t["errors"]:
                name.append(f"  !{t['errors']}", style="bold #f07882")
            name.append(
                "\n" + duration(t["end"] - t["start"]) + " · " + stamp(t["end"])[5:],
                style="dim",
            )
            name.append("\n" + t["id"], style="dim")
            entries.append((t["id"], (name,), 3))
        if self.ts:
            self.selected_trace = self.ts[index]["id"]
        else:
            self.selected_trace = None
        self.replace_rows(table, entries, index)
        self.rebuild_spans()

    def rebuild_spans(self):
        table = self.query_one("#waterfall", DataTable)
        trace = next((t for t in self.ts if t["id"] == self.selected_trace), None)
        self.rows = span_rows(trace["spans"], self.work) if trace else []
        keys = [f"{r[0]}:{r[1]['id']}:{r[1]['start']}" for r in self.rows]
        index = keys.index(self.selected_row) if self.selected_row in keys else 0
        entries = []
        spans = [r[1] for r in self.rows if r[0] == "span"]
        begin = min((s["start"] for s in spans), default=0)
        end = max((s["end"] for s in spans), default=1)
        for key, (kind, s, group, depth) in zip(keys, self.rows):
            if kind == "group":
                entries.append(
                    (
                        key,
                        (
                            Text("◇ task", style="bold #c4b5fd"),
                            Text(
                                clean(s["title"])
                                + (" [open]" if s["end"] is None else ""),
                                style="bold #c4b5fd",
                            ),
                            "",
                        ),
                        1,
                    )
                )
            else:
                offset = min(11, int((s["start"] - begin) / max(1, end - begin) * 12))
                size = max(
                    1,
                    min(
                        12 - offset,
                        int((s["end"] - s["start"]) / max(1, end - begin) * 12),
                    ),
                )
                color = (
                    "#fb7185"
                    if s["error"]
                    else "#a5b4fc"
                    if "session" in s["name"].lower()
                    else "#a7b8a8"
                )
                entries.append(
                    (
                        key,
                        (
                            Text(" " * offset + "━" * size, style=color),
                            Text(
                                "  " * depth + clean(s.get("label", s["name"])),
                                style=color,
                            ),
                            duration(s["end"] - s["start"]),
                        ),
                        1,
                    )
                )
        table.border_title = (
            "Task waterfall · a for session" if self.work else "Session waterfall"
        )
        self.selected_row = keys[index] if keys else None
        self.replace_rows(table, entries, index)
        self.render_details(index if keys else None)

    def replace_rows(self, table, entries, index):
        """Update content without treating refresh-generated highlights as navigation."""
        old_keys = [str(row.key.value) for row in table.ordered_rows]
        keys = [entry[0] for entry in entries]
        if old_keys == keys:
            for key, cells, height in entries:
                for column, value in zip(table.columns, cells):
                    if table.get_cell(key, column) != value:
                        table.update_cell(key, column, value)
            return
        scroll = (table.scroll_x, table.scroll_y)
        with table.prevent(DataTable.RowHighlighted):
            # A refresh restores state; it is not a cursor move. Bypass the
            # cursor watcher, which schedules scrolling even with scroll=False.
            table.set_reactive(DataTable.cursor_coordinate, Coordinate(0, 0))
            table.clear()
            for key, cells, height in entries:
                table.add_row(*cells, key=key, height=height)
            if keys:
                table.set_reactive(DataTable.cursor_coordinate, Coordinate(index, 0))
        expected = keys[index] if keys else None

        def restore_scroll():
            # Do not override navigation that happened after this refresh.
            current = self.selected_trace if table.id == "traces" else self.selected_row
            if current == expected:
                table.scroll_to(x=scroll[0], y=scroll[1], animate=False, force=True)

        self.call_after_refresh(restore_scroll)

    def render_details(self, index):
        trace = next((t for t in self.ts if t["id"] == self.selected_trace), None)
        lines = []
        if trace:
            lines = [
                "Trace",
                trace["id"],
                f"Elapsed (includes gaps): {duration(trace['end'] - trace['start'])}",
                f"Latest end: {stamp(trace['end'])}",
                "",
            ]
        if index is not None and index < len(self.rows):
            kind, s, _group, _depth = self.rows[index]
            if kind == "group":
                lines += [
                    "Reconstructed task",
                    s["title"],
                    f"Task: {s['id']}",
                    f"Start: {stamp(s['start'])}",
                    f"End: {stamp(s['end']) if s['end'] else 'Open'}",
                    s["basis"],
                ]
            else:
                lines += [
                    "Operation",
                    s["name"],
                    f"Duration: {duration(s['end'] - s['start'])}",
                    f"Start: {stamp(s['start'])}",
                    f"End: {stamp(s['end'])}",
                    f"Worker: {s['worker']}",
                    f"Service: {s['service']}",
                    f"Span: {s['id']}",
                    f"Parent: {s['parent'] or 'none'}",
                    "OTLP error status" if s["error"] else "No error status recorded",
                    "",
                ]
                lines += [f"{k}: {v}" for k, v in s.get("context", {}).items()]
                if s.get("work"):
                    lines += ["", "Reconstructed task: " + s["work"]["title"]]
        self.details_text = (
            "\n".join(clean(line) for line in lines) or "No matching traces."
        )
        self.query_one("#detail-text", Static).update(self.details_text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        key = str(event.row_key.value)
        table = event.data_table
        # Highlight messages are queued; an earlier rebuild can leave stale ones.
        if (
            not table.row_count
            or table.ordered_rows[table.cursor_row].key != event.row_key
        ):
            return
        if event.data_table.id == "traces":
            if key != self.selected_trace and any(t["id"] == key for t in self.ts):
                self.selected_trace, self.work, self.selected_row = key, None, None
                self.rebuild_spans()
                self.query_one("#details", Details).scroll_home(animate=False)
        elif key != self.selected_row and key in [
            f"{r[0]}:{r[1]['id']}:{r[1]['start']}" for r in self.rows
        ]:
            self.selected_row = key
            self.render_details(event.cursor_row)
            self.query_one("#details", Details).scroll_home(animate=False)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id == "traces":
            self.query_one("#waterfall").focus()
        elif event.cursor_row < len(self.rows):
            group = self.rows[event.cursor_row][2]
            if group:
                self.work, self.selected_row = group, None
                self.rebuild_spans()

    def on_input_changed(self, event: Input.Changed):
        self.work, self.selected_row = None, None
        self.rebuild_traces()
        self.update_status()

    def on_input_submitted(self, event: Input.Submitted):
        self.query_one("#traces").focus()

    def on_key(self, event):
        if isinstance(self.focused, Input):
            if event.key == "escape":
                self.query_one("#traces").focus()
            return
        panes = [
            self.query_one("#traces"),
            self.query_one("#waterfall"),
            self.query_one("#details"),
        ]
        if event.key in ("h", "l", "left", "right"):
            index = panes.index(self.focused) if self.focused in panes else 0
            panes[
                max(0, min(2, index + (-1 if event.key in ("h", "left") else 1)))
            ].focus()
        elif event.key in ("j", "k"):
            down = event.key == "j"
            if isinstance(self.focused, DataTable):
                (
                    self.focused.action_cursor_down
                    if down
                    else self.focused.action_cursor_up
                )()
            elif self.focused is panes[2]:
                (panes[2].action_scroll_down if down else panes[2].action_scroll_up)()
        elif event.key == "escape":
            self.action_session()
            self.query_one("#search", Input).value = ""
        else:
            return
        event.stop()
        event.prevent_default()

    def action_search(self):
        self.query_one("#search").focus()

    def action_range(self):
        self.hours = {24: 1, 1: 0, 0: 24}.get(self.hours, 24)
        self.rebuild_traces()
        self.update_status()

    def action_session(self):
        self.work, self.selected_row = None, None
        self.rebuild_spans()

    def action_pause(self):
        self.paused = not self.paused
        self.update_status()

    async def action_copy(self):
        if self.focused is not self.query_one("#details"):
            return
        text = self.details_text
        if shutil.which("pbcopy"):
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    ["pbcopy"],
                    input=text,
                    text=True,
                    check=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                self.notify(f"Clipboard failed: {exc}", severity="error")
                return
            self.notify("Copied full details to the local clipboard")
        else:
            self.copy_to_clipboard(text)
            self.notify("Details sent to terminal clipboard")
