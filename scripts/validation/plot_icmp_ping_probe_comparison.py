#!/usr/bin/env python3
"""Create ICMP ping-vs-probe comparison artifacts for validation runs."""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import json
import math
from pathlib import Path


PROBE_QUERY_PRIORITY = [
    "direct_median_1s_ms_by_kind",
    "direct_median_1s_ms",
    "direct_p50_ms_5s_by_kind",
    "direct_p50_ms_5s",
    "direct_mean_ms_5s",
    "direct_latest_ms",
    "direct_p95_ms_5s",
]


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="", encoding="utf-8")
    return path.open("r", newline="", encoding="utf-8")


def first_existing(results_dir: Path, names: list[str]) -> Path | None:
    for name in names:
        path = results_dir / name
        if path.exists():
            return path
    return None


def to_float(value, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def load_ping_summary(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def load_windows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    return [row for row in rows if row.get("level") == "direction" and row.get("traffic_type") == "icmp"]


def load_prometheus(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    with open_text(path) as fp:
        return list(csv.DictReader(fp))


def load_step_peer_ips(path: Path) -> dict[tuple[str, str], list[str]]:
    if not path.exists():
        return {}
    peer_ips: dict[tuple[str, str], list[str]] = {}
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("level") != "step" or event.get("phase") != "start":
                continue
            key = (event.get("scenario", ""), event.get("step", ""))
            ips = [str(ip) for ip in event.get("netem_peer_ips", []) if ip]
            if key[0] and key[1] and ips:
                peer_ips[key] = ips
    return peer_ips


def expected_mode(direction: str) -> str:
    # Ping is an end-to-end request/reply RTT. In this setup the RAN-side
    # probe view tracks the UE-visible ping RTT best for both launch directions.
    if direction in {"ue_to_upf", "upf_to_ue"}:
        return "ran"
    return ""


def metric_proto(row: dict[str, str]) -> str:
    return (row.get("proto") or row.get("protocol") or "").lower()


def select_probe_rows(prom: list[dict[str, str]], window: dict[str, str], peer_ips: list[str]) -> list[dict[str, str]]:
    start = to_float(window.get("start_epoch"), 0.0) or 0.0
    end = to_float(window.get("end_epoch"), start) or start
    mode = expected_mode(window.get("direction", ""))
    selected = []
    for row in prom:
        query_name = row.get("query_name", "")
        if query_name not in PROBE_QUERY_PRIORITY:
            continue
        timestamp = to_float(row.get("timestamp"))
        value = to_float(row.get("value"))
        if timestamp is None or value is None:
            continue
        if timestamp < start or timestamp > end:
            continue
        if mode and row.get("mode") and row.get("mode") != mode:
            continue
        ue_ip = row.get("ue_ip") or row.get("ip") or ""
        if peer_ips and ue_ip and ue_ip not in peer_ips:
            continue
        proto = metric_proto(row)
        if proto and proto not in {"icmp", "1"}:
            continue
        selected.append(row)
    return selected


def summarize_probe(rows: list[dict[str, str]]) -> tuple[str, float | None, int]:
    for query_name in PROBE_QUERY_PRIORITY:
        values = [to_float(row.get("value")) for row in rows if row.get("query_name") == query_name]
        values = [value for value in values if value is not None]
        if values:
            return query_name, sum(values) / len(values), len(values)
    return "", None, 0


def build_comparison(results_dir: Path) -> list[dict[str, object]]:
    ping_rows = load_ping_summary(results_dir / "ping_rtt_summary.csv")
    windows = load_windows(results_dir / "timeline_summary.csv")
    prom_path = first_existing(
        results_dir,
        [
            "prometheus_timeseries.csv",
            "prometheus_timeseries.csv.gz",
            "prometheus_timeseries_1s.csv",
            "prometheus_timeseries_1s.csv.gz",
        ],
    )
    prom = load_prometheus(prom_path)
    peer_ips_by_step = load_step_peer_ips(results_dir / "timeline_events.jsonl")
    windows_by_key = {
        (row.get("scenario"), row.get("step"), row.get("direction")): row for row in windows
    }

    comparison = []
    for ping in ping_rows:
        key = (ping.get("scenario"), ping.get("step"), ping.get("direction"))
        window = windows_by_key.get(key)
        probe_query = ""
        probe_mean = None
        probe_samples = 0
        if window:
            peer_ips = peer_ips_by_step.get((ping.get("scenario", ""), ping.get("step", "")), [])
            probe_query, probe_mean, probe_samples = summarize_probe(select_probe_rows(prom, window, peer_ips))
        comparison.append(
            {
                "scenario": ping.get("scenario", ""),
                "step": ping.get("step", ""),
                "direction": ping.get("direction", ""),
                "ue": ping.get("ue", ""),
                "ping_count": ping.get("count", ""),
                "ping_mean_ms": ping.get("mean_ms", ""),
                "ping_p50_ms": ping.get("p50_ms", ""),
                "ping_p95_ms": ping.get("p95_ms", ""),
                "probe_query": probe_query,
                "probe_mean_ms": "" if probe_mean is None else f"{probe_mean:.6f}",
                "probe_samples": probe_samples,
                "expected_probe_mode": expected_mode(ping.get("direction", "")),
            }
        )
    return comparison


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    fields = [
        "scenario",
        "step",
        "direction",
        "ue",
        "ping_count",
        "ping_mean_ms",
        "ping_p50_ms",
        "ping_p95_ms",
        "probe_query",
        "probe_mean_ms",
        "probe_samples",
        "expected_probe_mode",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def plot_rows_for_comparison(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        row
        for row in rows
        if to_float(row.get("ping_mean_ms")) is not None and to_float(row.get("probe_mean_ms")) is not None
    ]


def write_svg_plot(rows: list[dict[str, object]], path: Path) -> tuple[bool, str]:
    plot_rows = plot_rows_for_comparison(rows)
    if not plot_rows:
        path.with_suffix(".txt").write_text(
            "No overlapping ping and probe ICMP latency samples were available for plotting.\n",
            encoding="utf-8",
        )
        return False, ""

    svg_path = path.with_suffix(".svg")
    width = max(720, len(plot_rows) * 190)
    height = 430
    margin_left = 70
    margin_right = 30
    margin_top = 44
    margin_bottom = 82
    chart_width = width - margin_left - margin_right
    chart_height = height - margin_top - margin_bottom
    all_values = []
    for row in plot_rows:
        all_values.extend([
            to_float(row.get("ping_mean_ms"), 0.0) or 0.0,
            to_float(row.get("probe_mean_ms"), 0.0) or 0.0,
        ])
    y_max = max(all_values) * 1.25 if all_values else 1.0
    if y_max <= 0:
        y_max = 1.0

    def y_pos(value: float) -> float:
        return margin_top + chart_height - (value / y_max) * chart_height

    group_width = chart_width / max(1, len(plot_rows))
    bar_width = min(42, group_width * 0.28)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#17202a}.axis{stroke:#34495e;stroke-width:1}.grid{stroke:#d6dbdf;stroke-width:1}.ping{fill:#2e86ab}.probe{fill:#f28e2b}</style>',
        '<text x="70" y="26" font-size="18" font-weight="700">ICMP validation: ping RTT vs probe latency</text>',
        f'<line class="axis" x1="{margin_left}" y1="{margin_top + chart_height}" x2="{width - margin_right}" y2="{margin_top + chart_height}"/>',
        f'<line class="axis" x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + chart_height}"/>',
    ]
    for idx in range(5):
        value = y_max * idx / 4
        y = y_pos(value)
        parts.append(f'<line class="grid" x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{margin_left - 10}" y="{y + 4:.1f}" font-size="11" text-anchor="end">{value:.1f}</text>')

    for idx, row in enumerate(plot_rows):
        center = margin_left + group_width * idx + group_width / 2
        ping = to_float(row.get("ping_mean_ms"), 0.0) or 0.0
        probe = to_float(row.get("probe_mean_ms"), 0.0) or 0.0
        for cls, offset, value in [("ping", -bar_width / 1.7, ping), ("probe", bar_width / 1.7, probe)]:
            y = y_pos(value)
            h = margin_top + chart_height - y
            x = center + offset - bar_width / 2
            parts.append(f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{h:.1f}" rx="2"/>')
            parts.append(f'<text x="{x + bar_width / 2:.1f}" y="{y - 5:.1f}" font-size="10" text-anchor="middle">{value:.2f}</text>')
        label = html.escape(f"{row.get('direction', '')} {row.get('ue', '')}")
        parts.append(f'<text x="{center:.1f}" y="{height - 50}" font-size="12" text-anchor="middle">{label}</text>')

    legend_y = height - 24
    parts.extend([
        f'<rect class="ping" x="{margin_left}" y="{legend_y - 11}" width="14" height="14"/>',
        f'<text x="{margin_left + 20}" y="{legend_y}" font-size="12">ping mean RTT</text>',
        f'<rect class="probe" x="{margin_left + 150}" y="{legend_y - 11}" width="14" height="14"/>',
        f'<text x="{margin_left + 170}" y="{legend_y}" font-size="12">probe mean latency</text>',
        '<text transform="rotate(-90 18 230)" x="18" y="230" font-size="12" text-anchor="middle">Latency (ms)</text>',
        '</svg>',
    ])
    svg_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    path.with_suffix(".txt").write_text(
        f"Could not create PNG because matplotlib/numpy is unavailable; created SVG fallback: {svg_path.name}\n",
        encoding="utf-8",
    )
    return True, str(svg_path)


def write_plot(rows: list[dict[str, object]], path: Path) -> tuple[bool, str]:
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception:  # noqa: BLE001
        return write_svg_plot(rows, path)

    plot_rows = plot_rows_for_comparison(rows)
    if not plot_rows:
        path.with_suffix(".txt").write_text(
            "No overlapping ping and probe ICMP latency samples were available for plotting.\n",
            encoding="utf-8",
        )
        return False, ""

    labels = [f"{row['direction']}\n{row['ue']}" for row in plot_rows]
    ping_values = [to_float(row.get("ping_mean_ms"), 0.0) or 0.0 for row in plot_rows]
    probe_values = [to_float(row.get("probe_mean_ms"), 0.0) or 0.0 for row in plot_rows]
    x = np.arange(len(labels))
    width = 0.36

    fig, ax = plt.subplots(figsize=(max(7, len(labels) * 1.8), 4.5))
    ax.bar(x - width / 2, ping_values, width, label="ping mean RTT")
    ax.bar(x + width / 2, probe_values, width, label="probe mean latency")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("ICMP validation: ping RTT vs probe latency")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return True, str(path)


def write_markdown(rows: list[dict[str, object]], path: Path, plot_created: bool, plot_artifact: str) -> None:
    lines = [
        "# ICMP Ping vs Probe Validation",
        "",
        "Ping is UE-visible end-to-end ICMP RTT. Probe latency is measured at N3, so compare trend and distribution rather than exact equality.",
        "",
        f"Plot created: {plot_created}",
        f"Plot artifact: `{Path(plot_artifact).name}`" if plot_artifact else "Plot artifact: unavailable",
        "",
        "| Direction | UE | Ping mean ms | Ping p95 ms | Probe mean ms | Probe samples | Probe mode |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {direction} | {ue} | {ping_mean_ms} | {ping_p95_ms} | {probe_mean_ms} | {probe_samples} | {expected_probe_mode} |".format(
                **{key: row.get(key, "") for key in row}
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-plot", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    rows = build_comparison(results_dir)
    out_csv = Path(args.out_csv)
    out_plot = Path(args.out_plot)
    out_md = Path(args.out_md)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out_csv)
    plot_created, plot_artifact = write_plot(rows, out_plot)
    write_markdown(rows, out_md, plot_created, plot_artifact)
    print(json.dumps({"rows": len(rows), "csv": str(out_csv), "plot": plot_artifact or str(out_plot), "plot_created": plot_created}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
