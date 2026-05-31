#!/usr/bin/env python3
"""Summarize Grafana CSV exports for paper-ready latency results.

The Grafana "join by field" CSV export format is useful for dashboards but a
little awkward for papers: units are embedded in cells, UE labels live inside
wide column names, and exports often include warm-up/tail time outside the
actual experiment. This script normalizes those files into small audit tables
and SVG figures while keeping the raw exports untouched.
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


IMSI_TO_UE = {
    "001010000000006": "qhat01",
    "001010000000007": "qhat02",
    "001010000000008": "qhat03",
}

UE_FILE_TO_LABEL = {
    "UE1": "qhat01",
    "UE2": "qhat02",
    "UE3": "qhat03",
}

UE_FILE_TO_IMSI = {
    "UE1": "001010000000006",
    "UE2": "001010000000007",
    "UE3": "001010000000008",
}

VALUE_RE = re.compile(r"[-+]?(?:\d+(?:,\d{3})*|\d*)(?:\.\d+)?(?:[eE][-+]?\d+)?")


def read_grafana_csv(path: Path) -> pd.DataFrame:
    """Read a Grafana CSV, handling the optional leading sep=, row."""

    first = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[0]
    skiprows = 1 if first.strip().lower().startswith("sep=") else 0
    df = pd.read_csv(path, skiprows=skiprows, encoding="utf-8-sig")
    if "Time" in df.columns:
        df["Time"] = pd.to_datetime(df["Time"], errors="coerce")
    return df


def parse_number(value: object) -> float:
    """Extract a numeric value from Grafana cells such as '0.465 ms'."""

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return float("nan")
    text = str(value).replace("\ufeff", "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return float("nan")

    match = VALUE_RE.search(text.replace(" ", ""))
    if not match or match.group(0) in {"", "+", "-", "."}:
        return float("nan")

    try:
        number = float(match.group(0).replace(",", ""))
    except ValueError:
        return float("nan")

    unit = text[match.end() :].strip().lower()
    if "gb/s" in unit:
        return number * 1_000_000_000.0
    if "mb/s" in unit:
        return number * 1_000_000.0
    if "kb/s" in unit:
        return number * 1_000.0
    return number


def numeric_series(series: pd.Series) -> pd.Series:
    return series.map(parse_number).astype(float)


def stats(values: pd.Series) -> dict[str, float | int]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {
            "samples": 0,
            "mean": float("nan"),
            "median": float("nan"),
            "p05": float("nan"),
            "p95": float("nan"),
            "min": float("nan"),
            "max": float("nan"),
        }
    return {
        "samples": int(clean.shape[0]),
        "mean": float(clean.mean()),
        "median": float(clean.median()),
        "p05": float(clean.quantile(0.05)),
        "p95": float(clean.quantile(0.95)),
        "min": float(clean.min()),
        "max": float(clean.max()),
    }


def file_window(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if "Time" not in df.columns:
        return df.iloc[0:0].copy()
    return df[(df["Time"] >= start) & (df["Time"] <= end)].copy()


def split_label(label: str) -> list[str]:
    return [part.strip() for part in str(label).split(" | ")]


def ue_from_imsi(imsi: str) -> str:
    return IMSI_TO_UE.get(str(imsi), "unknown")


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_float(value: object, decimals: int = 3) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(num):
        return ""
    return f"{num:.{decimals}f}"


def summarize_series(
    *,
    rows: list[dict[str, object]],
    file_name: str,
    metric: str,
    unit: str,
    identity: dict[str, object],
    times: pd.Series,
    values: pd.Series,
) -> None:
    clean = pd.to_numeric(values, errors="coerce")
    nonempty = clean.dropna()
    item = {
        "file": file_name,
        "metric": metric,
        "unit": unit,
        **identity,
        **stats(clean),
        "first_sample": "",
        "last_sample": "",
    }
    if not nonempty.empty:
        item["first_sample"] = str(times.loc[nonempty.index].min())
        item["last_sample"] = str(times.loc[nonempty.index].max())
    rows.append(item)


def build_manifest(files: Iterable[Path], start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(files):
        df = read_grafana_csv(path)
        win = file_window(df, start, end)
        time_min = df["Time"].min() if "Time" in df else ""
        time_max = df["Time"].max() if "Time" in df else ""
        data_cols = [col for col in df.columns if col != "Time"]
        nonempty = 0
        for col in data_cols:
            nonempty += int(numeric_series(win[col]).notna().sum()) if col in win else 0
        rows.append(
            {
                "file": path.name,
                "rows_total": int(df.shape[0]),
                "rows_in_window": int(win.shape[0]),
                "series_columns": len(data_cols),
                "nonempty_points_in_window": nonempty,
                "export_start": str(time_min),
                "export_end": str(time_max),
                "analysis_start": str(start),
                "analysis_end": str(end),
            }
        )
    return rows


def summarize_latency_per_ue(files: Iterable[Path], start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    pattern = re.compile(r"^((?:Mean|P50|P95) Latency|Latency Event Rate) Per UE \((Uplink|Downlink)\)")
    metric_names = {
        "Mean Latency": ("mean_latency", "ms"),
        "P50 Latency": ("p50_latency", "ms"),
        "P95 Latency": ("p95_latency", "ms"),
        "Latency Event Rate": ("latency_event_rate", "events/s"),
    }

    for path in sorted(files):
        match = pattern.match(path.name)
        if not match:
            continue
        metric, unit = metric_names[match.group(1)]
        direction = match.group(2).lower()
        df = file_window(read_grafana_csv(path), start, end)
        for col in df.columns:
            if col == "Time":
                continue
            parts = split_label(col)
            if len(parts) < 4:
                continue
            imsi, ue_ip, slice_id, probe_role = parts[:4]
            summarize_series(
                rows=rows,
                file_name=path.name,
                metric=metric,
                unit=unit,
                identity={
                    "direction": direction,
                    "ue": ue_from_imsi(imsi),
                    "imsi": imsi,
                    "ue_ip": ue_ip,
                    "slice": slice_id,
                    "probe_role": probe_role,
                },
                times=df["Time"],
                values=numeric_series(df[col]),
            )
    return rows


def summarize_latency_per_slice(files: Iterable[Path], start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    pattern = re.compile(r"^((?:Mean|P50|P95) Latency|Latency Event Rate) Per Slice \((Uplink|Downlink)\)")
    metric_names = {
        "Mean Latency": ("mean_latency", "ms"),
        "P50 Latency": ("p50_latency", "ms"),
        "P95 Latency": ("p95_latency", "ms"),
        "Latency Event Rate": ("latency_event_rate", "events/s"),
    }

    for path in sorted(files):
        match = pattern.match(path.name)
        if not match:
            continue
        metric, unit = metric_names[match.group(1)]
        direction = match.group(2).lower()
        df = file_window(read_grafana_csv(path), start, end)
        for col in df.columns:
            if col == "Time":
                continue
            parts = split_label(col)
            if len(parts) < 2:
                continue
            slice_id, probe_role = parts[:2]
            summarize_series(
                rows=rows,
                file_name=path.name,
                metric=metric,
                unit=unit,
                identity={
                    "direction": direction,
                    "slice": slice_id,
                    "probe_role": probe_role,
                },
                times=df["Time"],
                values=numeric_series(df[col]),
            )
    return rows


def summarize_same_packet(files: Iterable[Path], start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    metric_map = [
        ("Mean Same-Packet UPF RTT Per UE", "mean_same_packet_upf_rtt", "ms"),
        ("Mean Same-Packet gNB RTT Per UE", "mean_same_packet_gnb_rtt", "ms"),
        ("Mean Same-Packet RTT Gap Per UE", "mean_same_packet_rtt_gap", "ms"),
        ("Latest Same-Packet UPF RTT Per UE", "latest_same_packet_upf_rtt", "ms"),
        ("Latest Same-Packet gNB RTT Per UE", "latest_same_packet_gnb_rtt", "ms"),
        ("Latest Same-Packet RTT Gap Per UE", "latest_same_packet_rtt_gap", "ms"),
        ("Same-Packet Pair Rate Per UE", "same_packet_pair_rate", "pairs/s"),
    ]

    for path in sorted(files):
        matched = next((item for item in metric_map if path.name.startswith(item[0])), None)
        if not matched:
            continue
        _, metric, unit = matched
        df = file_window(read_grafana_csv(path), start, end)
        for col in df.columns:
            if col == "Time":
                continue
            parts = split_label(col)
            if len(parts) < 6:
                continue
            imsi, ue_ip, slice_id, direction, probe_role, mode = parts[:6]
            summarize_series(
                rows=rows,
                file_name=path.name,
                metric=metric,
                unit=unit,
                identity={
                    "direction": direction,
                    "ue": ue_from_imsi(imsi),
                    "imsi": imsi,
                    "ue_ip": ue_ip,
                    "slice": slice_id,
                    "probe_role": probe_role,
                    "mode": mode,
                },
                times=df["Time"],
                values=numeric_series(df[col]),
            )
    return rows


def summarize_rejections(files: Iterable[Path], start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(files):
        if not path.name.startswith("Same-Packet Rejection Rate"):
            continue
        df = file_window(read_grafana_csv(path), start, end)
        for col in df.columns:
            if col == "Time":
                continue
            parts = split_label(col)
            if len(parts) < 3:
                continue
            direction, probe_role, reason = parts[:3]
            summarize_series(
                rows=rows,
                file_name=path.name,
                metric="same_packet_rejection_rate",
                unit="ops/s",
                identity={
                    "direction": direction,
                    "probe_role": probe_role,
                    "reason": reason,
                },
                times=df["Time"],
                values=numeric_series(df[col]),
            )
    return rows


def summarize_direct_ue_panels(files: Iterable[Path], start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    pattern = re.compile(r"^(UE\d+) Latency(?P<kind> \(Rolling Mean\)| Events_s)?-")
    metric_names = {
        None: ("direct_latency", "ms"),
        " (Rolling Mean)": ("direct_latency_rolling_mean", "ms"),
        " Events_s": ("direct_latency_event_rate", "events/s"),
    }

    for path in sorted(files):
        match = pattern.match(path.name)
        if not match:
            continue
        ue_panel = match.group(1)
        metric, unit = metric_names[match.group("kind")]
        df = file_window(read_grafana_csv(path), start, end)
        for col in df.columns:
            if col == "Time":
                continue
            parts = [part.strip() for part in col.split(",")]
            if len(parts) < 2:
                continue
            probe_side, probe_role = parts[:2]
            summarize_series(
                rows=rows,
                file_name=path.name,
                metric=metric,
                unit=unit,
                identity={
                    "ue_panel": ue_panel,
                    "ue": UE_FILE_TO_LABEL.get(ue_panel, ue_panel),
                    "imsi": UE_FILE_TO_IMSI.get(ue_panel, ""),
                    "probe_side": probe_side,
                    "probe_role": probe_role,
                },
                times=df["Time"],
                values=numeric_series(df[col]),
            )
    return rows


def summarize_system(files: Iterable[Path], start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    metric_files = {
        "CPU Usage": ("cpu_usage", "percent"),
        "Memory Usage": ("memory_usage", "percent"),
    }
    for path in sorted(files):
        matched = next((item for item in metric_files.items() if path.name.startswith(item[0])), None)
        if not matched:
            continue
        _, (metric, unit) = matched
        df = file_window(read_grafana_csv(path), start, end)
        for col in df.columns:
            if col == "Time":
                continue
            summarize_series(
                rows=rows,
                file_name=path.name,
                metric=metric,
                unit=unit,
                identity={"component": col},
                times=df["Time"],
                values=numeric_series(df[col]),
            )
    return rows


def summarize_radio_and_throughput(files: Iterable[Path], start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    rules = [
        ("SNR per RNTI", "snr_per_rnti", "dB"),
        ("PRACH I0", "prach_i0", "dB"),
        ("Downlink MCS per RNTI", "downlink_mcs_per_rnti", "index"),
        ("Uplink MCS per RNTI", "uplink_mcs_per_rnti", "index"),
        ("Downlink BLER", "downlink_bler_per_rnti", "percent"),
        ("Uplink BLER", "uplink_bler_per_rnti", "percent"),
        ("Downlink HARQ Errors", "downlink_harq_errors_per_rnti", "count"),
        ("Uplink HARQ Errors", "uplink_harq_errors_per_rnti", "count"),
        ("Downlink HARQ Retransmissions", "downlink_harq_retx_per_round_rnti", "count"),
        ("Uplink HARQ Retransmissions", "uplink_harq_retx_per_round_rnti", "count"),
        ("MAC Downlink Throughput from gNB", "mac_downlink_throughput_per_rnti", "bit/s"),
        ("MAC Uplink Throughput from gNB", "mac_uplink_throughput_per_rnti", "bit/s"),
        ("Total Downlink MAC Throughput", "total_downlink_mac_throughput", "bit/s"),
        ("Total Uplink MAC Throughput", "total_uplink_mac_throughput", "bit/s"),
    ]
    for path in sorted(files):
        matched = next((item for item in rules if path.name.startswith(item[0])), None)
        if not matched:
            continue
        _, metric, unit = matched
        df = file_window(read_grafana_csv(path), start, end)
        values = []
        active_series = 0
        for col in df.columns:
            if col == "Time":
                continue
            nums = numeric_series(df[col]).dropna()
            if not nums.empty:
                active_series += 1
                values.append(nums)
        combined = pd.concat(values, ignore_index=True) if values else pd.Series(dtype=float)
        row = {
            "file": path.name,
            "metric": metric,
            "unit": unit,
            "active_series": active_series,
            **stats(combined),
        }
        rows.append(row)
    return rows


def pivot_metric_summary(rows: list[dict[str, object]], index_cols: list[str], out_path: Path) -> None:
    if not rows:
        write_csv(out_path, [])
        return
    df = pd.DataFrame(rows)
    if df.empty:
        write_csv(out_path, [])
        return
    needed = [col for col in index_cols if col in df.columns]
    pivot = (
        df.pivot_table(
            index=needed,
            columns="metric",
            values="mean",
            aggfunc="first",
        )
        .reset_index()
        .sort_values(needed)
    )
    pivot.columns.name = None
    pivot.to_csv(out_path, index=False)


def nice_metric_label(metric: str) -> str:
    return metric.replace("_", " ")


def svg_grouped_bars(
    path: Path,
    title: str,
    y_label: str,
    groups: list[str],
    series_names: list[str],
    values_by_series: dict[str, list[float]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = max(900, 110 * len(groups) + 220)
    height = 520
    left, right, top, bottom = 88, 28, 58, 112
    plot_w = width - left - right
    plot_h = height - top - bottom
    palette = ["#2468B2", "#D65F3A", "#4B8F6B", "#7A5AA6"]
    all_vals = [
        value
        for name in series_names
        for value in values_by_series.get(name, [])
        if value is not None and not math.isnan(float(value))
    ]
    max_val = max(all_vals) if all_vals else 1.0
    max_val = max_val * 1.15 if max_val > 0 else 1.0
    group_w = plot_w / max(1, len(groups))
    bar_gap = 4
    bar_w = max(8, (group_w * 0.72 - bar_gap * (len(series_names) - 1)) / max(1, len(series_names)))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>text{font-family:Arial,Helvetica,sans-serif;fill:#1f2933} .axis{stroke:#556;stroke-width:1} .grid{stroke:#d9dee7;stroke-width:1} .bar{shape-rendering:geometricPrecision}</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{width/2:.1f}" y="30" text-anchor="middle" font-size="18" font-weight="700">{html.escape(title)}</text>',
        f'<text x="20" y="{top + plot_h/2:.1f}" transform="rotate(-90 20 {top + plot_h/2:.1f})" text-anchor="middle" font-size="13">{html.escape(y_label)}</text>',
    ]

    for i in range(6):
        frac = i / 5
        y = top + plot_h - frac * plot_h
        label = max_val * frac
        lines.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}"/>')
        lines.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11">{label:.2g}</text>')

    lines.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}"/>')
    lines.append(f'<line class="axis" x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}"/>')

    for gi, group in enumerate(groups):
        cx = left + gi * group_w + group_w / 2
        start_x = cx - ((bar_w * len(series_names)) + bar_gap * (len(series_names) - 1)) / 2
        for si, name in enumerate(series_names):
            vals = values_by_series.get(name, [])
            value = vals[gi] if gi < len(vals) else float("nan")
            if value is None or math.isnan(float(value)):
                continue
            bar_h = (float(value) / max_val) * plot_h
            x = start_x + si * (bar_w + bar_gap)
            y = top + plot_h - bar_h
            color = palette[si % len(palette)]
            lines.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="{color}"/>')
        lines.append(
            f'<text x="{cx:.1f}" y="{top + plot_h + 25}" text-anchor="middle" font-size="11">{html.escape(group)}</text>'
        )

    legend_x = left
    legend_y = height - 36
    for si, name in enumerate(series_names):
        color = palette[si % len(palette)]
        x = legend_x + si * 130
        lines.append(f'<rect x="{x}" y="{legend_y - 11}" width="12" height="12" fill="{color}"/>')
        lines.append(f'<text x="{x + 18}" y="{legend_y}" font-size="12">{html.escape(name)}</text>')

    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def create_figures(out_dir: Path, latency_rows: list[dict[str, object]], same_packet_rows: list[dict[str, object]]) -> None:
    figures_dir = out_dir / "figures"
    latency = pd.DataFrame(latency_rows)
    if not latency.empty:
        primary = latency[
            latency["ue"].astype(str).str.startswith("qhat")
            & (latency["slice"].astype(str) == "01:ffffff")
            & latency["metric"].isin(["mean_latency", "p95_latency", "latency_event_rate"])
        ].copy()
        if not primary.empty:
            primary["group"] = primary["ue"] + " " + primary["direction"].str.slice(0, 2).str.upper()
            for metric, title, ylabel, out_name in [
                ("mean_latency", "Mean latency by UE and direction", "ms", "fig_mean_latency_by_ue_direction.svg"),
                ("p95_latency", "P95 latency by UE and direction", "ms", "fig_p95_latency_by_ue_direction.svg"),
                (
                    "latency_event_rate",
                    "Latency event rate by UE and direction",
                    "events/s",
                    "fig_latency_event_rate_by_ue_direction.svg",
                ),
            ]:
                data = primary[primary["metric"] == metric]
                if data.empty:
                    continue
                groups = sorted(data["group"].unique())
                roles = [role for role in ["gnb", "upf"] if role in set(data["probe_role"])]
                values = {}
                for role in roles:
                    role_df = data[data["probe_role"] == role].set_index("group")
                    values[role] = [float(role_df["mean"].get(group, float("nan"))) for group in groups]
                svg_grouped_bars(figures_dir / out_name, title, ylabel, groups, roles, values)

    same = pd.DataFrame(same_packet_rows)
    if not same.empty:
        primary = same[
            same["ue"].astype(str).str.startswith("qhat")
            & (same["slice"].astype(str) == "01:ffffff")
            & same["metric"].isin(
                [
                    "mean_same_packet_upf_rtt",
                    "mean_same_packet_gnb_rtt",
                    "mean_same_packet_rtt_gap",
                    "same_packet_pair_rate",
                ]
            )
        ].copy()
        if not primary.empty:
            primary["group"] = primary["ue"] + " " + primary["direction"].str.slice(0, 2).str.upper()
            for metric, title, ylabel, out_name in [
                (
                    "mean_same_packet_upf_rtt",
                    "Same-packet UPF RTT by UE and direction",
                    "ms",
                    "fig_same_packet_upf_rtt.svg",
                ),
                (
                    "mean_same_packet_gnb_rtt",
                    "Same-packet gNB RTT by UE and direction",
                    "ms",
                    "fig_same_packet_gnb_rtt.svg",
                ),
                (
                    "same_packet_pair_rate",
                    "Same-packet pair rate by UE and direction",
                    "pairs/s",
                    "fig_same_packet_pair_rate.svg",
                ),
            ]:
                data = primary[primary["metric"] == metric]
                if data.empty:
                    continue
                groups = sorted(data["group"].unique())
                roles = sorted(data["probe_role"].unique())
                values = {}
                for role in roles:
                    role_df = data[data["probe_role"] == role].set_index("group")
                    values[role] = [float(role_df["mean"].get(group, float("nan"))) for group in groups]
                svg_grouped_bars(figures_dir / out_name, title, ylabel, groups, roles, values)


def generate_notes(
    out_dir: Path,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    manifest: list[dict[str, object]],
    latency_rows: list[dict[str, object]],
) -> None:
    manifest_df = pd.DataFrame(manifest)
    export_start = manifest_df["export_start"].min() if not manifest_df.empty else ""
    export_end = manifest_df["export_end"].max() if not manifest_df.empty else ""
    latency_df = pd.DataFrame(latency_rows)
    primary = pd.DataFrame()
    if not latency_df.empty:
        primary = latency_df[
            latency_df["ue"].astype(str).str.startswith("qhat")
            & (latency_df["slice"].astype(str) == "01:ffffff")
            & (latency_df["metric"] == "mean_latency")
        ].copy()

    coverage_lines: list[str] = []
    if not primary.empty:
        for _, row in primary.sort_values(["ue", "direction", "probe_role"]).iterrows():
            coverage_lines.append(
                "- "
                f"{row['ue']} {row['direction']} {row['probe_role']}: "
                f"{row['samples']} samples, {row['first_sample']} to {row['last_sample']}"
            )

    notes = [
        "# Grafana CSV paper notes",
        "",
        f"Analysis window: `{start}` to `{end}`.",
        f"Export coverage observed across files: `{export_start}` to `{export_end}`.",
        "",
        "## What to use in the paper",
        "",
        "- Use `latency_per_ue_timeavg.csv` as the main result table: qhat01/02/03, uplink/downlink, gNB vs UPF, mean/P50/P95/event-rate.",
        "- Use the generated SVGs in `figures/` for first-pass paper plots. They are vector graphics, so they can be restyled for LaTeX/Word later.",
        "- Treat slice `01:ffffff` as a controlled constant. This run should not claim slice-to-slice differentiation because only one real slice is present.",
        "- Use `same_packet_timeavg.csv` and `same_packet_rejection_summary.csv` as validation/supporting evidence for the same-packet RTT decomposition.",
        "- Use `radio_rnti_summary.csv` only as supporting radio-condition context. RNTIs churn during a run, so do not present those columns as stable per-UE identities unless you have a separate RNTI-to-UE map.",
        "- Use `system_overhead_summary.csv` for a lightweight overhead paragraph/table.",
        "",
        "## Watch-outs",
        "",
        "- The Grafana exports extend beyond the stated experiment end; this script filters the analysis to the requested window.",
        "- Some panels contain `unknown` IMSI or slice `0` during warm-up. Keep those out of primary figures unless you explicitly discuss unmapped traffic.",
        "- qhat03 appears much later than qhat01/qhat02 in the direct latency data, so full-window averages are not equal-duration comparisons.",
        "",
        "## UE latency coverage after filtering",
        "",
        *(coverage_lines or ["- No primary per-UE latency samples were found in the requested window."]),
        "",
        "## Suggested figure order",
        "",
        "1. Mean/P95 latency decomposition by UE and direction.",
        "2. Latency event rate by UE and direction.",
        "3. Same-packet UPF/gNB RTT and pair rate as validation evidence.",
        "4. Radio-condition and system-overhead summaries in an appendix or evaluation-support subsection.",
    ]
    (out_dir / "paper_notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-dir", default="CSVGrafana", help="Directory containing Grafana CSV exports")
    parser.add_argument("--start", default="2026-05-01 15:40:00")
    parser.add_argument("--end", default="2026-05-01 16:48:00")
    parser.add_argument("--out-dir", default="paper_artifacts/grafana_2026-05-01_1540_1648")
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    out_dir = Path(args.out_dir)
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    files = sorted(csv_dir.glob("*.csv"))
    if not files:
        raise SystemExit(f"No CSV files found in {csv_dir}")
    if end <= start:
        raise SystemExit("--end must be after --start")

    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(files, start, end)
    latency_ue = summarize_latency_per_ue(files, start, end)
    latency_slice = summarize_latency_per_slice(files, start, end)
    same_packet = summarize_same_packet(files, start, end)
    rejections = summarize_rejections(files, start, end)
    direct_panels = summarize_direct_ue_panels(files, start, end)
    system = summarize_system(files, start, end)
    radio = summarize_radio_and_throughput(files, start, end)

    write_csv(out_dir / "manifest.csv", manifest)
    write_csv(out_dir / "latency_per_ue_summary.csv", latency_ue)
    write_csv(out_dir / "latency_per_slice_summary.csv", latency_slice)
    write_csv(out_dir / "same_packet_summary.csv", same_packet)
    write_csv(out_dir / "same_packet_rejection_summary.csv", rejections)
    write_csv(out_dir / "direct_ue_panel_summary.csv", direct_panels)
    write_csv(out_dir / "system_overhead_summary.csv", system)
    write_csv(out_dir / "radio_rnti_summary.csv", radio)

    pivot_metric_summary(
        latency_ue,
        ["direction", "ue", "imsi", "ue_ip", "slice", "probe_role"],
        out_dir / "latency_per_ue_timeavg.csv",
    )
    pivot_metric_summary(
        same_packet,
        ["direction", "ue", "imsi", "ue_ip", "slice", "probe_role", "mode"],
        out_dir / "same_packet_timeavg.csv",
    )

    create_figures(out_dir, latency_ue, same_packet)
    generate_notes(out_dir, start=start, end=end, manifest=manifest, latency_rows=latency_ue)

    print(f"Wrote paper artifacts to {out_dir}")
    print(f"Read {len(files)} CSV files")
    print(f"Window: {start} to {end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
