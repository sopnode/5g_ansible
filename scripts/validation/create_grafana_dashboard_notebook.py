#!/usr/bin/env python3
"""Create a Grafana-style Jupyter notebook from exported Prometheus CSV data."""

from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path


def md(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": textwrap.dedent(source).strip().splitlines(True),
    }


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": textwrap.dedent(source).strip().splitlines(True),
    }


def build_notebook(title: str) -> dict:
    cells = [
        md(
            f"""
            # {title}

            This notebook recreates the monitoring dashboard as static figures
            from the exported Prometheus CSV files. It does not query Grafana;
            it uses only artifacts in this result directory.

            The most useful workflow is:
            1. Run the setup cells.
            2. Run **Full-Run Dashboard** for the whole campaign.
            3. Run **Per-Scenario Dashboard Figures** to generate the same
               dashboard panels for each scenario window.
            """
        ),
        code(
            r"""
            from pathlib import Path
            import json
            import math
            import re

            import numpy as np
            import pandas as pd
            import matplotlib.pyplot as plt

            RESULTS_DIR = Path(".").resolve()
            FIG_DIR = RESULTS_DIR / "grafana_dashboard_figures"
            FIG_DIR.mkdir(exist_ok=True)

            IMSI_TO_UE = {
                "001010000000006": "qhat01",
                "001010000000007": "qhat02",
                "001010000000008": "qhat03",
                "001010000000010": "qhat21",
                "001010000000011": "qhat22",
                "001010000001121": "uesim01",
                "001010000001122": "uesim02",
                "001010000001123": "uesim03",
            }

            IMSI_SUFFIX_TO_UE = {
                imsi[-10:]: ue
                for imsi, ue in IMSI_TO_UE.items()
            }

            UE_IP_TO_UE = {
                "12.1.0.1": "qhat01",
                "12.1.0.2": "qhat02",
                "12.1.0.3": "qhat03",
            }

            UE_COLORS = {
                "qhat01": "#1f77b4",
                "qhat02": "#d62728",
                "qhat03": "#2ca02c",
                "qhat21": "#9467bd",
                "qhat22": "#8c564b",
                "uesim01": "#1f77b4",
                "uesim02": "#d62728",
                "uesim03": "#2ca02c",
                "unknown": "#7f7f7f",
            }

            plt.rcParams.update({
                "figure.dpi": 120,
                "savefig.dpi": 240,
                "savefig.bbox": "tight",
                "savefig.pad_inches": 0.04,
                "font.family": "DejaVu Sans",
                "font.size": 8.5,
                "axes.labelsize": 9,
                "axes.titlesize": 9.5,
                "axes.titleweight": "bold",
                "axes.spines.top": False,
                "axes.spines.right": False,
                "axes.grid": True,
                "grid.alpha": 0.24,
                "legend.fontsize": 7,
            })

            def safe_name(text):
                return re.sub(r"[^A-Za-z0-9_.=-]+", "_", str(text)).strip("_") or "figure"

            def first_existing(*patterns):
                for pattern in patterns:
                    matches = sorted(RESULTS_DIR.glob(pattern))
                    if matches:
                        return matches[0]
                return None

            def read_json(path):
                path = Path(path)
                return json.loads(path.read_text()) if path.exists() else {}

            LABEL_COLS = [
                "query_name", "__name__", "imsi", "ue_ip", "slice", "probe_role",
                "mode", "direction", "reason", "teid", "ue_rnti", "rnti", "round",
                "instance", "pod", "namespace", "container", "job", "stream",
                "metric_json",
            ]

            def normalize_imsi(value):
                text = str(value).strip()
                if not text or text.lower() in {"nan", "none", "null"}:
                    return ""
                if re.fullmatch(r"\d+\.0+", text):
                    text = text.split(".", 1)[0]
                digits = re.sub(r"\D", "", text)
                if not digits:
                    return ""
                normalized = digits.zfill(15)
                if normalized in IMSI_TO_UE:
                    return normalized
                for suffix in IMSI_SUFFIX_TO_UE:
                    if digits.endswith(suffix) or normalized.endswith(suffix):
                        return "00101" + suffix
                return normalized

            def ue_from_labels(imsi, ue_ip):
                normalized = normalize_imsi(imsi)
                if normalized in IMSI_TO_UE:
                    return IMSI_TO_UE[normalized]
                return UE_IP_TO_UE.get(str(ue_ip).strip(), "unknown")

            def load_prometheus_csv(path):
                path = Path(path)
                if not path.exists():
                    return pd.DataFrame()
                dtype = {col: "string" for col in LABEL_COLS}
                try:
                    df = pd.read_csv(path, dtype=dtype)
                except pd.errors.ParserError as exc:
                    print(f"Warning: {path.name} is not clean CSV: {exc}")
                    print("Retrying with the slower Python CSV parser and skipping malformed lines.")
                    print("For paper figures, regenerate prometheus_timeseries.csv if this happened after a disk-quota failure.")
                    df = pd.read_csv(path, dtype=dtype, engine="python", on_bad_lines="skip")
                if df.empty:
                    return df
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True, errors="coerce")
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
                for col in LABEL_COLS:
                    if col not in df.columns:
                        df[col] = ""
                    df[col] = df[col].fillna("").astype(str)
                df["imsi_normalized"] = df["imsi"].map(normalize_imsi)
                df["ue"] = [
                    ue_from_labels(imsi, ue_ip)
                    for imsi, ue_ip in zip(df["imsi"], df["ue_ip"])
                ]
                df["logical_direction"] = df["direction"]
                df.loc[df["logical_direction"].eq("") & df["mode"].eq("core"), "logical_direction"] = "uplink"
                df.loc[df["logical_direction"].eq("") & df["mode"].eq("ran"), "logical_direction"] = "downlink"
                return df

            def compact_label(row):
                pieces = []
                for col in ["ue", "ue_ip", "imsi_normalized", "slice", "logical_direction", "probe_role", "mode", "rnti", "round", "container", "pod", "reason", "stream"]:
                    val = str(row.get(col, "")).strip()
                    if val and val not in {"unknown", "nan", "None"}:
                        pieces.append(f"{col}={val}" if col not in {"ue"} else val)
                return " | ".join(pieces) or str(row.get("query_name", "series"))

            def add_series_label(df):
                if df.empty:
                    return df
                df = df.copy()
                label_source = df.drop_duplicates(["query_name", "metric_json"], keep="first")
                label_map = {
                    (row["query_name"], row["metric_json"]): compact_label(row)
                    for row in label_source.to_dict("records")
                }
                df["series"] = [
                    label_map.get((query_name, metric_json), str(query_name))
                    for query_name, metric_json in zip(df["query_name"], df["metric_json"])
                ]
                return df

            def savefig(fig, out_dir, stem):
                out_dir = Path(out_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                for ext in ["png", "pdf", "svg"]:
                    fig.savefig(out_dir / f"{stem}.{ext}")

            full_csv = first_existing(
                "prometheus_timeseries.csv",
                "prometheus_timeseries_1s.csv",
                "prometheus_timeseries.csv.gz",
                "prometheus*.csv",
                "prometheus*.csv.gz",
            )
            print("Results:", RESULTS_DIR)
            print("Full-run Prometheus CSV:", full_csv)
            timeline = pd.read_csv(RESULTS_DIR / "timeline_summary.csv") if (RESULTS_DIR / "timeline_summary.csv").exists() else pd.DataFrame()
            if not timeline.empty:
                for col in ["start_epoch", "end_epoch", "duration_s", "parallel_streams"]:
                    if col in timeline.columns:
                        timeline[col] = pd.to_numeric(timeline[col], errors="coerce")
                timeline["start_time"] = pd.to_datetime(timeline["start_epoch"], unit="s", utc=True, errors="coerce")
                timeline["end_time"] = pd.to_datetime(timeline["end_epoch"], unit="s", utc=True, errors="coerce")
                display(timeline)
            scenario_windows = timeline[timeline["level"].eq("scenario")].copy() if not timeline.empty else pd.DataFrame()
            task_windows = timeline[timeline["level"].eq("iperf_task")].copy() if not timeline.empty else pd.DataFrame()

            def window_label(row):
                label = str(row.get("scenario", "")).strip() or str(row.get("name", "")).strip()
                if row.get("step", "") and row.get("level", "") != "scenario":
                    label += f" / {row.get('step')}"
                if row.get("direction", ""):
                    label += f" / {row.get('direction')}"
                if row.get("ue", ""):
                    label += f" / {row.get('ue')}"
                if not label:
                    label = str(row.get("window_id", "window"))
                return label

            def annotate_scenario_windows(ax, xmin=None, xmax=None, windows=None):
                if windows is None:
                    windows = scenario_windows
                if windows is None or windows.empty:
                    return
                colors = ["#dbeafe", "#dcfce7", "#fee2e2", "#fef3c7", "#ede9fe", "#e0f2fe"]
                xmin = pd.to_datetime(xmin, utc=True) if xmin is not None else None
                xmax = pd.to_datetime(xmax, utc=True) if xmax is not None else None
                for idx, row in windows.sort_values("start_time").reset_index(drop=True).iterrows():
                    start = row.get("start_time")
                    end = row.get("end_time")
                    if pd.isna(start) or pd.isna(end):
                        continue
                    if xmin is not None and end < xmin:
                        continue
                    if xmax is not None and start > xmax:
                        continue
                    ax.axvspan(start, end, color=colors[idx % len(colors)], alpha=0.18, zorder=0)
                    mid = start + (end - start) / 2
                    ax.text(
                        mid,
                        1.01,
                        window_label(row),
                        transform=ax.get_xaxis_transform(),
                        ha="center",
                        va="bottom",
                        fontsize=6.5,
                        rotation=0,
                        bbox={"boxstyle": "round,pad=0.18", "fc": "white", "ec": colors[idx % len(colors)], "alpha": 0.86},
                        clip_on=False,
                    )
            """
        ),
        md("## Scenario And Task Index"),
        code(
            r"""
            if scenario_windows.empty:
                print("No scenario windows found. Run the timeline-enabled playbook to create timeline_summary.csv.")
            else:
                cols = ["scenario", "scenario_title", "start_local", "end_local", "duration_s", "window_id"]
                display(scenario_windows[[c for c in cols if c in scenario_windows.columns]])

            if not task_windows.empty:
                cols = ["scenario", "step", "direction", "ue", "parallel_streams", "start_local", "end_local", "duration_s"]
                sort_cols = [c for c in ["start_epoch", "scenario", "step", "ue"] if c in task_windows.columns]
                display(
                    task_windows.sort_values(sort_cols)[[c for c in cols if c in task_windows.columns]]
                    if sort_cols
                    else task_windows[[c for c in cols if c in task_windows.columns]]
                )
            """
        ),
        md("## Dashboard Panel Definitions"),
        code(
            r"""
            DASHBOARD_SECTIONS = [
                {
                    "name": "Latency",
                    "panels": [
                        ("Direct latest RTT", [r"^direct_latest_ms$"], "Latency (ms)"),
                        ("Direct mean RTT", [r"^direct_mean_ms_5s$", r"direct.*mean.*ms"], "Latency (ms)"),
                        ("Direct median/p50 RTT", [r"^direct_median_1s_ms", r"^direct_p50_ms_5s", r"direct.*(median|p50).*ms"], "Latency (ms)"),
                        ("Direct p95 RTT", [r"^direct_p95_ms_5s$", r"direct.*p95.*ms"], "Latency (ms)"),
                        ("Direct p99 RTT", [r"^direct_p99_ms_5s$", r"direct.*p99.*ms"], "Latency (ms)"),
                        ("Latency sample rate", [r"^direct_event_rate_hz_5s$", r"direct.*event.*rate"], "Samples/s"),
                        ("Lost observation events", [r"lost.*event"], "Events/s"),
                    ],
                },
                {
                    "name": "Same-Packet Pairing",
                    "panels": [
                        ("Latest UPF RTT", [r"same_packet_upf.*latest"], "RTT (ms)"),
                        ("Latest gNB RTT", [r"same_packet_gnb.*latest"], "RTT (ms)"),
                        ("Latest RTT gap", [r"same_packet_gap.*latest"], "Gap (ms)"),
                        ("Mean UPF RTT", [r"same_packet_upf.*mean"], "RTT (ms)"),
                        ("Mean gNB RTT", [r"same_packet_gnb.*mean"], "RTT (ms)"),
                        ("Mean RTT gap", [r"same_packet_gap.*mean"], "Gap (ms)"),
                        ("Pair rate", [r"same_packet_pair_rate"], "Pairs/s"),
                        ("Rejection rate", [r"same_packet.*reject"], "Events/s"),
                    ],
                },
                {
                    "name": "RAN / Radio",
                    "panels": [
                        ("Slice throughput", [r"slice_throughput"], "bit/s"),
                        ("MAC throughput per RNTI", [r"mac_throughput_per_rnti", r"rf_mac_throughput"], "bit/s"),
                        ("Total MAC throughput", [r"mac_throughput_total"], "bit/s"),
                        ("Downlink/Uplink MCS", [r"mcs"], "MCS"),
                        ("Downlink/Uplink BLER", [r"bler"], "BLER"),
                        ("HARQ rounds/retransmissions", [r"harq|rounds"], "Count"),
                        ("HARQ errors", [r"errors"], "Count"),
                        ("Connected UEs", [r"number_ues"], "UEs"),
                        ("PRB saturation", [r"saturation|prb"], "Percent"),
                        ("SNR / SINR", [r"snr|sinr"], "dB"),
                        ("RSRP / RSRQ", [r"rsrp|rsrq"], "dB"),
                        ("PRACH I0", [r"prach"], "dB"),
                    ],
                },
                {
                    "name": "Monitoring Overhead",
                    "panels": [
                        ("Container CPU", [r"container_cpu"], "CPU cores"),
                        ("Container memory", [r"container_memory"], "MiB"),
                    ],
                },
            ]

            def available_queries(df):
                if df.empty or "query_name" not in df:
                    return []
                return sorted(df["query_name"].dropna().unique())

            def choose_query(df, patterns):
                names = available_queries(df)
                for pattern in patterns:
                    regex = re.compile(pattern, re.I)
                    matches = [name for name in names if regex.search(name)]
                    if matches:
                        return matches[0]
                return None

            def plot_panel(ax, df, query_name, title, ylabel, max_series=10, smooth=3, mark_scenarios=True):
                subset = df[df["query_name"].eq(query_name)].dropna(subset=["timestamp", "value"]).copy()
                if subset.empty:
                    ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
                    ax.set_title(title)
                    if mark_scenarios:
                        annotate_scenario_windows(ax)
                    return
                counts = subset.groupby("series")["value"].count().sort_values(ascending=False)
                keep = set(counts.head(max_series).index)
                subset = subset[subset["series"].isin(keep)].sort_values("timestamp")
                for label, group in subset.groupby("series", sort=False):
                    y = group["value"].rolling(smooth, min_periods=1, center=True).mean()
                    color = UE_COLORS.get(group["ue"].iloc[0], None) if "ue" in group else None
                    ax.plot(group["timestamp"], y, linewidth=1.5, label=label[:80], color=color)
                ax.set_title(title)
                ax.set_ylabel(ylabel)
                ax.legend(loc="best", fontsize=6)
                if mark_scenarios:
                    annotate_scenario_windows(ax, subset["timestamp"].min(), subset["timestamp"].max())

            def plot_section(df, section, out_dir, prefix, scenario_label="", mark_scenarios=True):
                panels = []
                missing = []
                for title, patterns, ylabel in section["panels"]:
                    query = choose_query(df, patterns)
                    if query:
                        panels.append((title, query, ylabel))
                    else:
                        missing.append(title)
                if not panels:
                    print(f"[{section['name']}] no matching exported data")
                    return missing
                cols = 2
                rows = int(math.ceil(len(panels) / cols))
                fig, axes = plt.subplots(rows, cols, figsize=(14, max(3.2, rows * 3.2)), squeeze=False)
                for ax in axes.flat:
                    ax.axis("off")
                for ax, (title, query, ylabel) in zip(axes.flat, panels):
                    ax.axis("on")
                    plot_panel(ax, df, query, title, ylabel, mark_scenarios=mark_scenarios)
                    ax.text(0.01, 0.98, query, transform=ax.transAxes, va="top", fontsize=6, alpha=0.65)
                heading = section["name"] if not scenario_label else f"{section['name']} - {scenario_label}"
                fig.suptitle(heading, fontsize=13, fontweight="bold")
                fig.autofmt_xdate()
                plt.tight_layout()
                stem = safe_name(f"{prefix}_{section['name']}")
                savefig(fig, out_dir, stem)
                plt.show()
                return missing

            def plot_dashboard(csv_path, name="full_run", out_dir=None, mark_scenarios=True):
                out_dir = Path(out_dir or FIG_DIR / safe_name(name))
                df = add_series_label(load_prometheus_csv(csv_path))
                if df.empty:
                    print(f"No data in {csv_path}")
                    return
                print(f"Dashboard: {name}")
                print("CSV:", csv_path)
                print("Time:", df["timestamp"].min(), "->", df["timestamp"].max())
                print("Queries:", len(available_queries(df)))
                display(
                    df.groupby("query_name").agg(
                        rows=("value", "size"),
                        series=("series", "nunique"),
                        first=("timestamp", "min"),
                        last=("timestamp", "max"),
                    ).sort_values(["rows", "series"], ascending=False)
                )
                missing_rows = []
                for section in DASHBOARD_SECTIONS:
                    missing = plot_section(
                        df,
                        section,
                        out_dir,
                        safe_name(name),
                        scenario_label=name,
                        mark_scenarios=mark_scenarios,
                    )
                    for panel in missing or []:
                        missing_rows.append({"section": section["name"], "panel": panel})
                if missing_rows:
                    print("Panels without matching exported query:")
                    display(pd.DataFrame(missing_rows))
            """
        ),
        md("## Full-Run Dashboard"),
        code(
            r"""
            if full_csv is not None:
                plot_dashboard(full_csv, name="full_run", out_dir=FIG_DIR / "full_run")
            else:
                print("No Prometheus CSV found in this result directory.")
            """
        ),
        md("## Per-Scenario Dashboard Figures"),
        code(
            r"""
            scenario_root = RESULTS_DIR / "by_window" / "scenario"
            scenario_csvs = sorted(
                list(scenario_root.glob("*/prometheus_timeseries.csv"))
                + list(scenario_root.glob("*/prometheus_timeseries.csv.gz"))
            )
            print(f"Scenario windows found: {len(scenario_csvs)}")
            for csv_path in scenario_csvs:
                scenario_name = csv_path.parent.name
                print("\\n" + "=" * 90)
                print("Scenario deep dive:", scenario_name)
                print("=" * 90)
                metadata_path = csv_path.parent / "window_metadata.json"
                if metadata_path.exists():
                    display(read_json(metadata_path).get("window", {}))
                plot_dashboard(csv_path, name=scenario_name, out_dir=FIG_DIR / "scenarios" / scenario_name, mark_scenarios=True)
            """
        ),
        md("## Optional: Per-Task Dashboard Figures"),
        code(
            r"""
            MAKE_TASK_FIGURES = False

            if MAKE_TASK_FIGURES:
                task_root = RESULTS_DIR / "by_window" / "task"
                task_csvs = sorted(
                    list(task_root.glob("*/prometheus_timeseries.csv"))
                    + list(task_root.glob("*/prometheus_timeseries.csv.gz"))
                )
                print(f"Task windows found: {len(task_csvs)}")
                for csv_path in task_csvs:
                    task_name = csv_path.parent.name
                    plot_dashboard(csv_path, name=task_name, out_dir=FIG_DIR / "tasks" / task_name)
            else:
                print("Set MAKE_TASK_FIGURES = True and rerun this cell to generate one dashboard per task/direction.")
            """
        ),
        md(
            """
            ## Notes

            If a panel says `No data`, it means the exported CSV does not
            contain a matching `query_name` for that panel. The solution is to
            add that PromQL expression to `paper_prometheus_queries.json` or
            rely on the metric-discovery export if the raw metric is enough.
            """
        ),
    ]

    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "pygments_lexer": "ipython3",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--title", default="Grafana-Style Dashboard Figures")
    parser.add_argument("--output-name", default="grafana_dashboard_figures.ipynb")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / args.output_name
    out.write_text(json.dumps(build_notebook(args.title), indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
