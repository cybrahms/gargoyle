"""Read-only enrichment from local Crush records; never invent OTLP parents."""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

SECRET = re.compile(
    r"(?i)(\b(?:token|password|secret|api[_-]?key|authorization)\b\s*[:=]\s*)(\S+)"
)


def safe(value, limit=600):
    return SECRET.sub(r"\1[REDACTED]", str(value or ""))[:limit]


def obj(value):
    try:
        x = json.loads(value) if isinstance(value, str) else value
        return x if isinstance(x, dict) else {}
    except (ValueError, TypeError):
        return {}


def enrich(spans, workers_root=None):
    if workers_root is None:
        return
    workers_root = Path(workers_root).expanduser().resolve()
    sessions = {}
    for s in spans:
        if s.get("session"):
            sessions.setdefault((s["worker"], s["session"]), []).append(s)
    for (worker, sid), ss in sessions.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", worker):
            continue
        db = workers_root / worker / "data/crush.db"
        if not db.is_file():
            continue
        try:
            with closing(
                sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=1)
            ) as conn:
                rows = conn.execute(
                    "SELECT role, parts, created_at FROM messages WHERE session_id=? ORDER BY created_at, rowid",
                    (sid,),
                ).fetchall()
            calls, results = [], {}
            for role, parts, timestamp in rows:
                for part in json.loads(parts):
                    d = obj(part.get("data", {}))
                    if part.get("type") == "tool_call":
                        calls.append(
                            {
                                "id": d.get("id"),
                                "name": d.get("name"),
                                "args": obj(d.get("input")),
                                "at": timestamp * 1000,
                            }
                        )
                    elif part.get("type") == "tool_result":
                        results[d.get("tool_call_id")] = {
                            "body": obj(d.get("content")),
                            "error": d.get("is_error"),
                            "at": timestamp * 1000,
                        }
            groups = []
            for c in calls:
                result = results.get(c["id"], {})
                if (
                    c["name"]
                    and c["name"].endswith("_claim")
                    and result.get("body", {}).get("id")
                    and not result.get("error")
                ):
                    b = result["body"]
                    payload = b.get("payload", {})
                    pr = payload.get("pull_request", {})
                    repo = payload.get("repository", {}).get("full_name") or pr.get(
                        "base", {}
                    ).get("repo", {}).get("full_name")
                    number = payload.get("number") or pr.get("number")
                    title = (
                        f"Review {repo} #{number}: {pr.get('title', '')}"
                        if repo and number
                        else b.get("summary") or b.get("title") or f"Task {b['id']}"
                    )
                    groups.append(
                        {
                            "id": b["id"],
                            "title": safe(title),
                            "start": c["at"],
                            "end": None,
                            "basis": "Reconstructed from successful task claim and matching completion",
                        }
                    )
                if (
                    c["name"]
                    and c["name"].endswith("_complete")
                    and result
                    and not result.get("error")
                ):
                    for g in groups:
                        if g["id"] == c["args"].get("id") and g["end"] is None:
                            g["end"] = result["at"]
            index = {}
            for c in calls:
                index.setdefault((c["name"], c["at"]), []).append(c)
            for s in ss:
                matches = index.get((s["name"], s["start"]), [])
                if len(matches) == 1:
                    c = matches[0]
                    args = c["args"]
                    r = results.get(c["id"], {})
                    body = r.get("body", {})
                    context = {
                        "Source": "Crush tool call matched by session, tool name and exact start time",
                        "Call ID": c["id"],
                    }
                    for key in (
                        "title",
                        "path",
                        "file_path",
                        "command",
                        "description",
                        "id",
                    ):
                        if key in args:
                            context[key.replace("_", " ").title()] = safe(args[key])
                    if "artifact" in (c["name"] or ""):
                        for key in ("title", "url", "id"):
                            if key in body:
                                context["Artifact " + key] = safe(body[key])
                    if c["name"] and c["name"].endswith("_complete"):
                        for key in ("summary", "note", "result"):
                            if key in args:
                                context[key.title()] = safe(args[key])
                    if r:
                        context["Tool result"] = (
                            "Error" if r.get("error") else "Returned successfully"
                        )
                    s["context"] = context
                    if args.get("title"):
                        s["label"] = s["name"] + " · " + safe(args["title"], 180)
                    elif body.get("title") and "artifact" in s["name"]:
                        s["label"] = s["name"] + " · " + safe(body["title"], 180)
                candidates = [
                    g
                    for g in groups
                    if g["start"] <= s["start"]
                    and (g["end"] is None or s["start"] <= g["end"])
                ]
                if len(candidates) == 1:
                    s["work"] = candidates[0]
                elif len(candidates) > 1:
                    s["context"] = dict(
                        s.get("context", {}),
                        Attribution="Multiple active claims; work grouping is ambiguous",
                    )
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            continue
