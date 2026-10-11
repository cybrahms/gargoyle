# CLI audit: 0.3.0

The receiver used `--source` for a write destination. The audit found the same
ambiguity in command discovery and filtering. This release changes the interface
without compatibility aliases.

| Area | Decision | Reason |
| --- | --- | --- |
| Commands | Real `view`, `receive`, `query` subcommands | Root help now lists commands and command help shows the full invocation. |
| Receiver path | `--output DIRECTORY` | Incoming exports are written here. Reader-only options are rejected. |
| Reader path | `--input PATH` | Files are read here; a file or directory is accepted. Writer-only options are rejected. |
| Default data location | `GARGOYLE_DATA_DIR`, then `~/.local/state/gargoyle` | One application-owned directory, shared by all three commands. |
| Trace selection | `--trace-id ID` | The value is an exact ID, not trace content or a query expression. |
| Time range | `--since 30m/1h/7d` or `--all` | Removes the undocumented numeric sentinel; time uses latest span end. |
| Error filter | `--with-errors` | Keeps entire traces containing errors, including successful spans. |
| Optional enrichment | `--crush-workers-dir DIRECTORY` | Names the integration and documents its expected database layout. |
| Search | Keep `--search TEXT` | Help names searched fields, substring matching, and whole-trace scope. |
| Span output | Keep `--spans` | Help states it changes output from summaries to all spans in matching traces. |
| Demo | Reject conflicting input/enrichment/filter flags | Prevents options from being silently ignored, including `--input=PATH` syntax. |
| Bind/size flags | Keep names; explain units, defaults and effects | `--host`, `--port`, and MiB limits describe receiver settings. |
| Abbreviated options | Disable argparse abbreviations | Removed flags cannot survive accidentally as prefixes of new ones. |

No arguments still opens the viewer. CLI tests cover directionality, shared
defaults, filter scope, help discovery, rejected old names and conflicting demo
flags. The TUI range shortcut also works after an arbitrary `--since` duration.
