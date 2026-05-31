#!/usr/bin/env python3
"""Summarize bpftool before/after snapshots into packet-path overhead rows."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


FIELDNAMES = [
    "label",
    "scenario",
    "step",
    "direction",
    "namespace",
    "pod",
    "container",
    "prog_id",
    "prog_name",
    "prog_type",
    "tag",
    "run_cnt_start",
    "run_cnt_end",
    "run_cnt_delta",
    "run_time_ns_start",
    "run_time_ns_end",
    "run_time_ns_delta",
    "ns_per_run",
    "start_file",
    "end_file",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bpftool-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser.parse_args()


def read_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    raw_stdout = payload.get("raw_stdout") or ""
    programs: list[dict[str, Any]] = []
    if raw_stdout:
        decoded: Any = raw_stdout
        if isinstance(raw_stdout, str):
            try:
                decoded = json.loads(raw_stdout)
            except json.JSONDecodeError:
                decoded = []
        if isinstance(decoded, list):
            programs = [item for item in decoded if isinstance(item, dict)]
        elif isinstance(decoded, dict):
            nested = decoded.get("programs") or decoded.get("prog") or []
            if isinstance(nested, list):
                programs = [item for item in nested if isinstance(item, dict)]

    payload["programs"] = programs
    payload["source_file"] = str(path)
    return payload


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def program_key(program: dict[str, Any]) -> str:
    prog_id = program.get("id")
    if prog_id is not None:
        return f"id:{prog_id}"
    return "|".join(
        str(program.get(key, ""))
        for key in ("name", "type", "tag", "loaded_at")
    )


def index_programs(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {program_key(program): program for program in snapshot.get("programs", [])}


def summarize_pair(start: dict[str, Any], end: dict[str, Any]) -> list[dict[str, Any]]:
    start_programs = index_programs(start)
    end_programs = index_programs(end)
    rows: list[dict[str, Any]] = []

    for key in sorted(set(start_programs) & set(end_programs)):
        before = start_programs[key]
        after = end_programs[key]

        run_cnt_start = number(before.get("run_cnt"))
        run_cnt_end = number(after.get("run_cnt"))
        run_time_start = number(before.get("run_time_ns"))
        run_time_end = number(after.get("run_time_ns"))

        run_cnt_delta = None
        if run_cnt_start is not None and run_cnt_end is not None:
            run_cnt_delta = run_cnt_end - run_cnt_start

        run_time_delta = None
        if run_time_start is not None and run_time_end is not None:
            run_time_delta = run_time_end - run_time_start

        ns_per_run = None
        if run_cnt_delta and run_time_delta is not None and run_cnt_delta > 0:
            ns_per_run = run_time_delta / run_cnt_delta

        rows.append(
            {
                "label": start.get("label", ""),
                "scenario": start.get("scenario", ""),
                "step": start.get("step", ""),
                "direction": start.get("direction", ""),
                "namespace": end.get("namespace") or start.get("namespace", ""),
                "pod": end.get("pod") or start.get("pod", ""),
                "container": end.get("container") or start.get("container", ""),
                "prog_id": after.get("id") or before.get("id") or "",
                "prog_name": after.get("name") or before.get("name") or "",
                "prog_type": after.get("type") or before.get("type") or "",
                "tag": after.get("tag") or before.get("tag") or "",
                "run_cnt_start": run_cnt_start if run_cnt_start is not None else "",
                "run_cnt_end": run_cnt_end if run_cnt_end is not None else "",
                "run_cnt_delta": run_cnt_delta if run_cnt_delta is not None else "",
                "run_time_ns_start": run_time_start if run_time_start is not None else "",
                "run_time_ns_end": run_time_end if run_time_end is not None else "",
                "run_time_ns_delta": run_time_delta if run_time_delta is not None else "",
                "ns_per_run": ns_per_run if ns_per_run is not None else "",
                "start_file": start.get("source_file", ""),
                "end_file": end.get("source_file", ""),
            }
        )

    return rows


def main() -> int:
    args = parse_args()
    snapshots: dict[tuple[str, str], dict[str, Any]] = {}

    if args.bpftool_dir.exists():
        for path in sorted(args.bpftool_dir.glob("*.json")):
            snapshot = read_snapshot(path)
            if not snapshot:
                continue
            label = str(snapshot.get("label") or path.stem.rsplit("__", 1)[0])
            phase = str(snapshot.get("phase") or path.stem.rsplit("__", 1)[-1])
            snapshots[(label, phase)] = snapshot

    rows: list[dict[str, Any]] = []
    labels = sorted({label for label, _phase in snapshots})
    for label in labels:
        start = snapshots.get((label, "start"))
        end = snapshots.get((label, "end"))
        if start and end:
            rows.extend(summarize_pair(start, end))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} bpftool overhead rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
