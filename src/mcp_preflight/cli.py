from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .findings import LEVELS
from .lint import leena_action_name
from .run import Report, parse_headers_json, run_preflight

MARK = {"info": "i", "warn": "!", "error": "x"}


def render(rep: Report) -> str:
    lines = [f"mcp-preflight  {rep.url}", ""]
    if rep.server_info:
        lines.append(f"server     {rep.server_info.get('name', '?')} {rep.server_info.get('version', '')}".rstrip())
        lines.append(f"protocol   {rep.protocol_version}   session id: {'yes' if rep.session_id else 'no (stateless)'}")
    lines.append(f"auth       {rep.auth}")
    if rep.latency_ms.get("runs"):
        lt = rep.latency_ms
        lines.append(f"latency    initialize+tools/list p50 {lt['p50']} ms, max {lt['max']} ms over {lt['runs']} runs"
                     f" ({lt.get('content_type') or '?'})")
    if rep.discovery:
        d = rep.discovery
        lines.append(f"oauth      via {d.source or 'nothing'}; auth server {d.auth_server}")
        for u, st in d.steps:
            lines.append(f"             GET {u} -> {st}")
        if d.as_metadata:
            lines.append(f"             authorize {d.as_metadata.get('authorization_endpoint')}")
            lines.append(f"             token     {d.as_metadata.get('token_endpoint')}")
    if rep.tools:
        lines += ["", f"tools ({len(rep.tools)})  ->  Leena action name"]
        w = max(len(t["name"]) for t in rep.tools)
        for t in rep.tools:
            lines.append(f"  {t['name']:<{w}}  ->  {leena_action_name(t['name'])}")
    if rep.findings:
        lines += ["", "findings"]
        for f in sorted(rep.findings, key=lambda f: (-LEVELS.index(f.level), f.code, f.subject)):
            lines.append(f"  [{MARK[f.level]}] {f.code} {f.subject}: {f.message}")
    counts = {lvl: sum(f.level == lvl for f in rep.findings) for lvl in LEVELS}
    lines += ["", f"verdict    {rep.verdict}  ({counts['error']} errors, {counts['warn']} warnings, {counts['info']} notes)"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mcp-preflight",
        description="Check a remote MCP server before registering it in Leena AI's Custom MCP connector.",
    )
    p.add_argument("url", help="MCP remote URL (Streamable HTTP endpoint)")
    p.add_argument("--header", action="append", default=[], metavar="'Name: value'",
                   help="extra request header; repeatable")
    p.add_argument("--headers-json", metavar="JSON",
                   help="headers exactly as you'd paste them into Leena's 'Authentication headers' field")
    p.add_argument("--runs", type=int, default=3, help="sessions to time (default 3)")
    p.add_argument("--slow-ms", type=float, default=3000, help="warn when p50 exceeds this (default 3000)")
    p.add_argument("--snapshot", type=Path, help="tool-list snapshot file for drift checks")
    p.add_argument("--update-snapshot", action="store_true", help="overwrite the snapshot with this run")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.add_argument("--fail-on", choices=["warn", "error"], default="error",
                   help="exit 1 at this level or worse (default error)")
    a = p.parse_args(argv)

    headers: dict[str, str] = {}
    pre = []
    if a.headers_json:
        headers, pre = parse_headers_json(a.headers_json)
    for h in a.header:
        name, sep, value = h.partition(":")
        if not sep:
            p.error(f"--header needs 'Name: value', got {h!r}")
        headers[name.strip()] = value.strip()

    if pre:
        rep = Report(url=a.url, auth="headers", findings=pre)
    else:
        rep = run_preflight(a.url, headers=headers or None, runs=a.runs, slow_ms=a.slow_ms,
                            snapshot_path=a.snapshot, update_snapshot=a.update_snapshot)
    print(json.dumps(rep.as_dict(), indent=2) if a.json else render(rep))

    threshold = LEVELS.index(a.fail_on)
    return 1 if any(LEVELS.index(f.level) >= threshold for f in rep.findings) else 0


if __name__ == "__main__":
    sys.exit(main())
