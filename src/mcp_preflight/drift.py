"""Tool-list drift between two runs.

Leena discovers tools at runtime, so "the available actions can change whenever
the server adds, removes, or updates its tools". An AOP that names a removed
tool breaks quietly. Snapshot the tool list after you register a server, then
run preflight in CI against the snapshot.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .findings import Finding


def fingerprint(tool: dict) -> str:
    body = {"description": tool.get("description", ""), "inputSchema": tool.get("inputSchema", {})}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]


def snapshot(tools: list[dict]) -> dict:
    return {
        t["name"]: {"fp": fingerprint(t), "required": sorted((t.get("inputSchema") or {}).get("required") or [])}
        for t in tools
    }


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def save(path: Path, snap: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")


def diff(old: dict, new: dict) -> list[Finding]:
    out: list[Finding] = []
    for name in sorted(old.keys() - new.keys()):
        out.append(Finding("error", "D001", name,
                           "Tool removed since snapshot; any AOP that references this action will break."))
    for name in sorted(new.keys() - old.keys()):
        out.append(Finding("info", "D002", name, "New tool since snapshot; it shows up as a new Leena action."))
    for name in sorted(old.keys() & new.keys()):
        o, n = old[name], new[name]
        added_req = sorted(set(n["required"]) - set(o["required"]))
        if added_req:
            out.append(Finding("warn", "D003", name,
                               f"New required input(s) {added_req}; existing AOP steps may not supply them."))
        elif o["fp"] != n["fp"]:
            out.append(Finding("info", "D004", name, "Description or schema changed since snapshot."))
    return out
