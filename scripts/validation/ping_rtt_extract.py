#!/usr/bin/env python3
"""Extract per-packet RTT samples from validation ping logs."""

from __future__ import annotations

import argparse
import csv
import io
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path


PING_PATH_RE = re.compile(
    r"scenario=(?P<scenario>[^/]+)/step=(?P<step>[^/]+)/direction=(?P<direction>[^/]+)/ping_(?P<ue>[^/.]+)\.log$"
)
PING_LINE_RE = re.compile(
    r"^(?:\[(?P<epoch>[0-9]+(?:\.[0-9]+)?)\]\s+)?"
    r".*(?:icmp_)?seq=(?P<seq>[0-9]+).*time[=<](?P<rtt_ms>[0-9.]+)\s*ms",
    re.IGNORECASE,
)

FIELDS = [
    "source",
    "scenario",
    "step",
    "direction",
    "ue",
    "seq",
    "rtt_ms",
    "epoch",
    "time_utc",
    "line",
]


def safe_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def time_utc(epoch: float | None) -> str:
    if epoch is None:
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def parse_log_text(text: str, source: str) -> list[dict[str, object]]:
    match = PING_PATH_RE.search(source)
    if not match:
        return []
    meta = match.groupdict()
    rows = []
    for line in text.splitlines():
        parsed = PING_LINE_RE.match(line.strip())
        if not parsed:
            continue
        epoch = safe_float(parsed.group("epoch"))
        rows.append(
            {
                "source": source,
                "scenario": meta["scenario"],
                "step": meta["step"],
                "direction": meta["direction"],
                "ue": meta["ue"],
                "seq": int(parsed.group("seq")),
                "rtt_ms": float(parsed.group("rtt_ms")),
                "epoch": "" if epoch is None else f"{epoch:.6f}",
                "time_utc": time_utc(epoch),
                "line": line.strip(),
            }
        )
    return rows


def iter_ping_logs(results_dir: Path):
    for path in results_dir.rglob("ping_*.log"):
        yield path.read_text(encoding="utf-8", errors="replace"), str(path.relative_to(results_dir))

    for archive in results_dir.glob("*.tgz"):
        try:
            with tarfile.open(archive, "r:gz") as tf:
                for member in tf.getmembers():
                    if not member.isfile() or not member.name.endswith(".log") or "ping_" not in member.name:
                        continue
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        continue
                    text = io.TextIOWrapper(extracted, encoding="utf-8", errors="replace").read()
                    yield text, f"{archive.name}:{member.name}"
        except tarfile.TarError:
            continue


def write_summary(rows: list[dict[str, object]], path: Path) -> None:
    groups: dict[tuple[str, str, str, str], list[float]] = {}
    for row in rows:
        key = (
            str(row["scenario"]),
            str(row["step"]),
            str(row["direction"]),
            str(row["ue"]),
        )
        groups.setdefault(key, []).append(float(row["rtt_ms"]))

    fields = [
        "scenario",
        "step",
        "direction",
        "ue",
        "count",
        "mean_ms",
        "min_ms",
        "p50_ms",
        "p95_ms",
        "max_ms",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for key, values in sorted(groups.items()):
            values = sorted(values)
            count = len(values)

            def percentile(q: float) -> float:
                if count == 1:
                    return values[0]
                idx = min(count - 1, max(0, round((count - 1) * q)))
                return values[idx]

            writer.writerow(
                {
                    "scenario": key[0],
                    "step": key[1],
                    "direction": key[2],
                    "ue": key[3],
                    "count": count,
                    "mean_ms": f"{sum(values) / count:.6f}",
                    "min_ms": f"{values[0]:.6f}",
                    "p50_ms": f"{percentile(0.50):.6f}",
                    "p95_ms": f"{percentile(0.95):.6f}",
                    "max_ms": f"{values[-1]:.6f}",
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary-out")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    rows: list[dict[str, object]] = []
    for text, source in iter_ping_logs(results_dir):
        rows.extend(parse_log_text(text, source))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    summary_out = Path(args.summary_out) if args.summary_out else out.with_name("ping_rtt_summary.csv")
    write_summary(rows, summary_out)
    print(f"wrote {len(rows)} ping RTT samples to {out}")
    print(f"wrote ping RTT summary to {summary_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
