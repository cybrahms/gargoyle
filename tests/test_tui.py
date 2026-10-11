"""Regression checks for time filtering and task attribution in the terminal view."""

import unittest

from scope.tui import clean, span_rows, traces


def span(sid, start, end, parent="", work=None):
    s = {
        "id": sid,
        "trace": "trace",
        "name": "tool",
        "start": start,
        "end": end,
        "worker": "worker",
        "service": "service",
        "error": False,
        "parent": parent,
    }
    if work:
        s["work"] = work
    return s


class ViewTest(unittest.TestCase):
    def test_recent_end_keeps_long_session(self):
        ss = [span("session", 0, 100000000)]
        self.assertEqual(len(traces(ss, hours=1, now=100001)), 1)
        self.assertEqual(traces(ss, hours=1, now=110000), [])
        self.assertEqual(len(traces(ss, query="WORKER", hours=0, now=110000)), 1)
        self.assertEqual(traces(ss, query="absent", hours=0), [])

    def test_reclaims_are_distinct_and_zoom_preserves_parent_depth(self):
        first = {"id": "task", "start": 10, "end": 20, "title": "one"}
        second = {"id": "task", "start": 30, "end": None, "title": "two"}
        ss = [
            span("parent", 0, 50),
            span("a", 11, 12, "parent", first),
            span("b", 31, 32, "parent", second),
        ]
        rows = span_rows(ss, ("task", 30))
        self.assertEqual([r[1]["id"] for r in rows], ["task", "b"])
        self.assertEqual(rows[1][3], 1)
        self.assertEqual(len([r for r in span_rows(ss) if r[0] == "group"]), 2)

    def test_parent_cycle_terminates(self):
        rows = span_rows([span("a", 0, 1, "b"), span("b", 0, 1, "a")])
        self.assertEqual([r[3] for r in rows], [1, 1])

    def test_terminal_controls_are_removed(self):
        self.assertNotIn("\x1b", clean("title\x1b[2J\nnext"))


class InteractionTest(unittest.IsolatedAsyncioTestCase):
    async def test_navigation_search_zoom_copy_and_refresh(self):
        from unittest.mock import patch

        from textual.widgets import DataTable, Input

        from scope.tui import Details, Inspector

        g = {
            "id": "task",
            "start": 10,
            "end": 20,
            "title": "Review example",
            "basis": "Claim evidence",
        }
        ss = [span("a", 11, 12, work=g), span("b", 13, 14, work=g)]
        for s in ss:
            s["end"] = __import__("time").time() * 1000

        class FakeStore:
            def read(self):
                return {"spans": ss, "files": 1, "warnings": []}

        app = Inspector(FakeStore())
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app.focused.id, "traces")
            app.hours = 0.5
            await pilot.press("t")
            self.assertEqual(app.hours, 24)
            await pilot.press("l")
            self.assertEqual(app.focused.id, "waterfall")
            await pilot.press("j")
            self.assertEqual(app.focused.cursor_row, 1)
            await pilot.press("k")
            self.assertEqual(app.focused.cursor_row, 0)
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.work, ("task", 10))
            await pilot.press("l")
            self.assertIsInstance(app.focused, Details)
            expected = app.details_text
            with (
                patch("scope.tui.shutil.which", return_value="/usr/bin/pbcopy"),
                patch("scope.tui.subprocess.run") as copied,
            ):
                await pilot.press("c")
                await pilot.pause()
                self.assertEqual(copied.call_args.kwargs["input"], expected)
            await pilot.press("h")
            self.assertEqual(app.focused.id, "waterfall")
            await pilot.press("slash")
            await pilot.press("h", "j", "k", "l", "c")
            self.assertEqual(app.query_one("#search", Input).value, "hjklc")
            await pilot.press("enter")
            self.assertEqual(app.focused.id, "traces")
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(len(app.ts), 1)
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            self.assertEqual(app.query_one("#waterfall", DataTable).row_count, 3)


class RefreshTest(unittest.IsolatedAsyncioTestCase):
    async def test_tick_preserves_trace_span_focus_and_all_scroll_positions(self):
        from textual.widgets import DataTable

        from scope.tui import Details, Inspector

        now = __import__("time").time() * 1000
        ss = []
        for tid in range(12):
            for sid in range(70):
                item = span(f"s{sid}", now - 100000 + sid * 100, now - tid * 1000)
                item["trace"] = f"t{tid}"
                item["context"] = {
                    f"Field {n}": "long detail value " * 5 for n in range(30)
                }
                ss.append(item)

        class FakeStore:
            def read(self):
                return {"spans": ss, "files": 1, "warnings": []}

        app = Inspector(FakeStore())
        async with app.run_test(size=(120, 24)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            trace_table = app.query_one("#traces", DataTable)
            span_table = app.query_one("#waterfall", DataTable)
            details = app.query_one("#details", Details)
            trace_table.move_cursor(row=8)
            await pilot.pause()
            await pilot.press("l")
            span_table.move_cursor(row=45)
            await pilot.pause()
            await pilot.press("l")
            details.scroll_to(y=20, animate=False)
            await pilot.pause()
            selected = (app.selected_trace, app.selected_row)
            scrolls = [
                (w.scroll_x, w.scroll_y) for w in (trace_table, span_table, details)
            ]
            for changed in (False, True, False):
                if changed:
                    extra = span("earlier", now - 200000, now + 1000)
                    extra["trace"] = "t8"
                    ss.append(extra)
                    next(s for s in ss if s["trace"] == "t8" and s["id"] == "s45")[
                        "context"
                    ]["Late result"] = "Returned successfully"
                app.auto_refresh()
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual((app.selected_trace, app.selected_row), selected)
                self.assertEqual(app.focused.id, "details")
                if changed:
                    self.assertIn(
                        "Late result: Returned successfully", app.details_text
                    )
                self.assertEqual(
                    [
                        (w.scroll_x, w.scroll_y)
                        for w in (trace_table, span_table, details)
                    ],
                    scrolls,
                )


if __name__ == "__main__":
    unittest.main()
