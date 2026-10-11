import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from gargoyle.cli import build_parser, main
from gargoyle.options import data_directory


class CliTest(unittest.TestCase):
    def test_directions_defaults_and_shared_filters(self):
        parser = build_parser()
        view = parser.parse_args(
            ["view", "--input", "exports", "--trace-id", "id", "--since", "30m"]
        )
        query = parser.parse_args(
            ["query", "--input", "exports", "--trace-id", "id", "--since", "30m"]
        )
        self.assertEqual(
            (view.input, view.trace_id, view.since),
            (query.input, query.trace_id, query.since),
        )
        self.assertEqual(query.since, 0.5)
        receive = parser.parse_args(["receive", "--output", "exports"])
        self.assertEqual(receive.output, query.input)
        self.assertFalse(hasattr(receive, "input"))
        self.assertFalse(hasattr(query, "output"))
        with patch.dict("os.environ", {"GARGOYLE_DATA_DIR": "~/custom-traces"}):
            parser = build_parser()
            for command, attribute in [
                ("view", "input"),
                ("query", "input"),
                ("receive", "output"),
            ]:
                self.assertEqual(
                    getattr(parser.parse_args([command]), attribute),
                    Path("~/custom-traces").expanduser(),
                )

    def test_old_names_wrong_directions_and_ambiguous_ranges_are_rejected(self):
        for argv in [
            ["receive", "--source", "data"],
            ["receive", "--input", "data"],
            ["query", "--output", "data"],
            ["view", "--source", "data"],
            ["query", "--hours", "0"],
            ["query", "--errors"],
            ["view", "--trace", "id"],
            ["query", "--since", "0h"],
            ["query", "--since", "yesterday"],
            ["query", "--since", "1h", "--all"],
        ]:
            with (
                self.subTest(argv=argv),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                build_parser().parse_args(argv)
            self.assertEqual(raised.exception.code, 2)

    def test_help_discovers_commands_and_uses_complete_command_name(self):
        for argv, expected in [
            (["--help"], "{view,receive,query}"),
            (["receive", "--help"], "gargoyle receive"),
            (["query", "--help"], "gargoyle query"),
        ]:
            output = io.StringIO()
            with (
                patch("sys.argv", ["gargoyle"]),
                redirect_stdout(output),
                self.assertRaises(SystemExit) as raised,
            ):
                main(argv)
            self.assertEqual(raised.exception.code, 0)
            self.assertIn(expected, output.getvalue())

    def test_demo_rejects_ignored_options_even_with_equals(self):
        for args in [
            ["--input", "data"],
            ["--input=data"],
            ["--all"],
            ["--since=1h"],
            ["--crush-workers-dir=data"],
        ]:
            with (
                self.subTest(args=args),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                main(["view", "--demo", *args])
            self.assertEqual(raised.exception.code, 2)

    def test_receive_output_is_passed_to_writer(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("gargoyle.receiver.make_server") as server,
            redirect_stdout(io.StringIO()),
        ):
            main(["receive", "--output", directory, "--port", "0"])
            self.assertEqual(server.call_args.args[0], Path(directory))
            server.return_value.__enter__.return_value.serve_forever.assert_called_once()

    def test_bare_command_opens_view_and_respects_shared_default(self):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("gargoyle.tui.Inspector") as inspector,
        ):
            main([])
            store = inspector.call_args.args[0]
            self.assertEqual(store.directory, data_directory())
            self.assertEqual(inspector.return_value.hours, 24)
            inspector.return_value.run.assert_called_once()
