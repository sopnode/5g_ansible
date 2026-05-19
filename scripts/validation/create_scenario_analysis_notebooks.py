#!/usr/bin/env python3
"""Create one self-contained analysis notebook inside every scenario folder."""

from __future__ import annotations

import argparse
import json
import re
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


def safe_title(name: str) -> str:
    return re.sub(r"[_-]+", " ", name).strip().title()


def build_notebook(scenario: str) -> dict:
    title = f"Scenario Analysis: {scenario}"
    cells = [
        md(
            f"""
            # {title}

            This notebook is scoped to **one scenario folder**. It loads only
            the files next to it: Prometheus metrics clipped to this scenario
            timeline, this scenario's timeline rows, and this scenario's iperf
            JSON/stderr files.

            Use it to correlate latency, RAN metrics, interference phases, and
            iperf behavior without mixing other scenarios into the same plots.
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

            SCENARIO_DIR = Path(".").resolve()
            FIG_DIR = SCENARIO_DIR / "figures"
            FIG_DIR.mkdir(exist_ok=True)

            IMSI_TO_UE = {
                "001010000000006": "qhat01",
                "001010000000007": "qhat02",
                "001010000000008": "qhat03",
                "001010000000010": "qhat21",
                "001010000000011": "qhat22",
            }
            UE_IP_TO_UE = {
                "12.1.0.1": "qhat01",
                "12.1.0.2": "qhat02",
                "12.1.0.3": "qhat03",
            }
            IMSI_SUFFIX_TO_UE = {imsi[-10:]: ue for imsi, ue in IMSI_TO_UE.items()}
            UE_COLORS = {
                "qhat01": "#1f77b4",
                "qhat02": "#d62728",
                "qhat03": "#2ca02c",
                "qhat21": "#9467bd",
                "qhat22": "#8c564b",
                "unknown": "#7f7f7f",
            }

            plt.rcParams.update({
                "figure.figsize": (12, 5),
                "figure.dpi": 120,
                "savefig.dpi": 220,
                "axes.grid": True,
                "grid.alpha": 0.25,
                "axes.spines.top": False,
                "axes.spines.right": False,
                "legend.frameon": False,
                "font.size": 8.5,
            })

            def savefig(name):
                FIG_DIR.mkdir(exist_ok=True)
                plt.tight_layout()
                for ext in ["png", "pdf"]:
                    plt.savefig(FIG_DIR / f"{name}.{ext}", bbox_inches="tight")

            def read_json(path):
                path = Path(path)
                return json.loads(path.read_text()) if path.exists() else {}

            def first_existing(*names):
                for name in names:
                    matches = sorted(SCENARIO_DIR.glob(name))
                    if matches:
                        return matches[0]
                return None

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

            metadata = read_json(SCENARIO_DIR / "scenario_metadata.json")
            print("Scenario folder:", SCENARIO_DIR)
            display(metadata)
            """
        ),
        md("## Scenario Timeline"),
        code(
            r"""
            timeline_path = SCENARIO_DIR / "timeline_summary.csv"
            timeline = pd.read_csv(timeline_path) if timeline_path.exists() else pd.DataFrame()
            if not timeline.empty:
                for col in ["start_epoch", "end_epoch", "duration_s", "parallel_streams"]:
                    if col in timeline.columns:
                        timeline[col] = pd.to_numeric(timeline[col], errors="coerce")
                timeline["start_time"] = pd.to_datetime(timeline["start_epoch"], unit="s", utc=True, errors="coerce")
                timeline["end_time"] = pd.to_datetime(timeline["end_epoch"], unit="s", utc=True, errors="coerce")
                display(timeline)
            else:
                print("No local timeline_summary.csv found.")

            phases = timeline[timeline["level"].isin(["interference_phase", "stress_phase"])].copy() if not timeline.empty else pd.DataFrame()
            iperf_windows = timeline[timeline["level"].eq("iperf_task")].copy() if not timeline.empty else pd.DataFrame()
            if not iperf_windows.empty:
                cols = ["step", "direction", "ue", "parallel_streams", "start_local", "end_local", "duration_s"]
                display(iperf_windows[[c for c in cols if c in iperf_windows.columns]])

            def annotate_phases(ax):
                if phases.empty:
                    return
                colors = {
                    "clean_before_noise": "#dbeafe",
                    "noise_on": "#fee2e2",
                    "recovery_after_noise": "#dcfce7",
                    "clean_before_stress": "#dbeafe",
                    "stress_on": "#fed7aa",
                    "recovery_after_stress": "#dcfce7",
                }
                ymin, ymax = ax.get_ylim()
                for _, row in phases.iterrows():
                    start = row.get("start_time")
                    end = row.get("end_time")
                    name = str(row.get("name", "phase"))
                    direction = str(row.get("direction", ""))
                    if pd.isna(start) or pd.isna(end):
                        continue
                    ax.axvspan(start, end, color=colors.get(name, "#f3f4f6"), alpha=0.22, linewidth=0)
                    ax.text(start, ymax, f"{name} {direction}", va="top", ha="left", fontsize=7, rotation=90, alpha=0.75)
                ax.set_ylim(ymin, ymax)
            """
        ),
        md("## Iperf Logs"),
        code(
            r"""
            def parse_iperf(path):
                payload = read_json(path)
                rel = path.relative_to(SCENARIO_DIR)
                parts = rel.parts
                step = parts[1] if len(parts) > 3 and parts[0] == "iperf" else ""
                direction = parts[2] if len(parts) > 3 and parts[0] == "iperf" else ""
                ue = path.stem.replace("iperf_", "")
                start = payload.get("start", {})
                end = payload.get("end", {})
                row = {
                    "ue": ue,
                    "step": step,
                    "direction": direction,
                    "path": str(path),
                    "error": payload.get("error", ""),
                    "reverse": start.get("test_start", {}).get("reverse", ""),
                    "num_streams": start.get("test_start", {}).get("num_streams", ""),
                    "duration_s": start.get("test_start", {}).get("duration", ""),
                    "start_time": start.get("timestamp", {}).get("time", ""),
                }
                for key in ["sum_sent", "sum_received"]:
                    summary = end.get(key, {}) if isinstance(end, dict) else {}
                    row[f"{key}_mbps"] = float(summary.get("bits_per_second", 0) or 0) / 1e6
                    row[f"{key}_bytes"] = summary.get("bytes", 0)
                    row[f"{key}_retransmits"] = summary.get("retransmits", np.nan)
                return row

            iperf_paths = sorted((SCENARIO_DIR / "iperf").glob("*/*/iperf_*.json"))
            iperf = pd.DataFrame([parse_iperf(path) for path in iperf_paths])
            if iperf.empty:
                print("No local iperf JSON logs found.")
            else:
                display(iperf)
                plot_df = iperf.copy()
                plot_df["throughput_mbps"] = np.where(
                    plot_df["sum_received_mbps"] > 0,
                    plot_df["sum_received_mbps"],
                    plot_df["sum_sent_mbps"],
                )
                ax = plot_df.pivot_table(
                    index=["step", "direction"],
                    columns="ue",
                    values="throughput_mbps",
                    aggfunc="mean",
                ).plot(kind="bar", figsize=(12, 4), color=[UE_COLORS.get(c, None) for c in sorted(plot_df["ue"].unique())])
                ax.set_ylabel("iperf throughput (Mb/s)")
                ax.set_title("iperf throughput by step, direction, and UE")
                plt.xticks(rotation=25, ha="right")
                savefig("iperf_throughput_by_step_direction_ue")
                plt.show()

                errors = iperf[iperf["error"].astype(str).str.len() > 0]
                if not errors.empty:
                    print("iperf errors:")
                    display(errors[["ue", "step", "direction", "error", "path"]])

            stderr_paths = sorted((SCENARIO_DIR / "iperf").glob("*/*/iperf_*.stderr"))
            stderr_rows = []
            for path in stderr_paths:
                text = path.read_text(errors="replace").strip()
                if text:
                    stderr_rows.append({"path": str(path), "stderr": text[:2000]})
            if stderr_rows:
                print("Non-empty iperf stderr files:")
                display(pd.DataFrame(stderr_rows))
            """
        ),
        md("## Prometheus Metrics"),
        code(
            r"""
            LABEL_COLS = [
                "query_name", "__name__", "imsi", "ue_ip", "slice", "probe_role",
                "mode", "direction", "reason", "teid", "ue_rnti", "rnti", "round",
                "instance", "pod", "namespace", "container", "job", "stream",
                "metric_json",
            ]

            def load_prometheus(path):
                if path is None or not path.exists():
                    return pd.DataFrame()
                dtype = {col: "string" for col in LABEL_COLS}
                try:
                    df = pd.read_csv(path, dtype=dtype)
                except pd.errors.ParserError:
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
                df["ue"] = [ue_from_labels(imsi, ue_ip) for imsi, ue_ip in zip(df["imsi"], df["ue_ip"])]
                df["logical_direction"] = df["direction"]
                df.loc[df["logical_direction"].eq("") & df["mode"].eq("core"), "logical_direction"] = "uplink"
                df.loc[df["logical_direction"].eq("") & df["mode"].eq("ran"), "logical_direction"] = "downlink"
                label_cols = ["ue", "ue_ip", "slice", "logical_direction", "probe_role", "mode", "rnti", "round", "container", "pod", "reason", "stream"]
                df["series"] = (
                    df[label_cols].astype(str)
                    .agg(" ".join, axis=1)
                    .str.replace(r"\s+", " ", regex=True)
                    .str.strip()
                )
                df.loc[df["series"].eq(""), "series"] = df["query_name"]
                return df

            prom_path = first_existing("prometheus_timeseries.csv.gz", "prometheus_timeseries.csv")
            print("Prometheus CSV:", prom_path)
            prom = load_prometheus(prom_path)
            if prom.empty:
                print("No Prometheus rows in this scenario folder.")
            else:
                query_summary = prom.groupby("query_name").agg(
                    rows=("value", "size"),
                    series=("series", "nunique"),
                    first=("timestamp", "min"),
                    last=("timestamp", "max"),
                    mean=("value", "mean"),
                    p95=("value", lambda s: s.dropna().quantile(0.95) if s.notna().any() else np.nan),
                ).sort_values(["rows", "series"], ascending=False)
                display(query_summary)
            """
        ),
        md("## Plot Helpers"),
        code(
            r"""
            def value_for_plot(df, query_name):
                values = df["value"].copy()
                q = str(query_name).lower()
                if q.endswith("_ns") or "latency" in q or "rtt" in q:
                    return values / 1e6, "ms"
                return values, "value"

            def plot_query(query_pattern, title=None, max_series=12, rolling="5s"):
                if prom.empty:
                    print("No Prometheus data loaded.")
                    return
                mask = prom["query_name"].str.contains(query_pattern, case=False, regex=True, na=False)
                df = prom[mask].dropna(subset=["timestamp", "value"]).copy()
                if df.empty:
                    print(f"No metrics matched: {query_pattern}")
                    return
                for query_name, qdf in df.groupby("query_name", sort=False):
                    fig, ax = plt.subplots(figsize=(12, 4))
                    qdf = qdf.sort_values("timestamp")
                    y, unit = value_for_plot(qdf, query_name)
                    qdf = qdf.assign(plot_value=y)
                    top_series = (
                        qdf.groupby("series")["plot_value"].count()
                        .sort_values(ascending=False)
                        .head(max_series)
                        .index
                    )
                    for series, sdf in qdf[qdf["series"].isin(top_series)].groupby("series"):
                        sdf = sdf.set_index("timestamp").sort_index()
                        plot_values = sdf["plot_value"]
                        if rolling:
                            plot_values = plot_values.rolling(rolling, min_periods=1).mean()
                        ue = str(sdf["ue"].iloc[0]) if "ue" in sdf else "unknown"
                        ax.plot(plot_values.index, plot_values.values, label=series, linewidth=1.4, color=UE_COLORS.get(ue))
                    annotate_phases(ax)
                    ax.set_title(title or query_name)
                    ax.set_ylabel(unit)
                    ax.legend(loc="best", ncol=2)
                    savefig(f"metric_{re.sub(r'[^A-Za-z0-9_.=-]+', '_', query_name)}")
                    plt.show()

            def box_query(query_pattern, title=None):
                if prom.empty:
                    return
                mask = prom["query_name"].str.contains(query_pattern, case=False, regex=True, na=False)
                df = prom[mask].dropna(subset=["value"]).copy()
                if df.empty:
                    print(f"No metrics matched: {query_pattern}")
                    return
                for query_name, qdf in df.groupby("query_name", sort=False):
                    y, unit = value_for_plot(qdf, query_name)
                    qdf = qdf.assign(plot_value=y)
                    group_cols = [c for c in ["ue", "logical_direction", "probe_role", "mode"] if c in qdf.columns]
                    qdf["group"] = qdf[group_cols].astype(str).agg(" ".join, axis=1).str.replace(r"\s+", " ", regex=True)
                    top = qdf.groupby("group")["plot_value"].count().sort_values(ascending=False).head(12).index
                    fig, ax = plt.subplots(figsize=(12, 4))
                    qdf[qdf["group"].isin(top)].boxplot(column="plot_value", by="group", ax=ax, rot=35)
                    ax.set_title(title or f"{query_name} distribution")
                    ax.set_ylabel(unit)
                    fig.suptitle("")
                    savefig(f"box_{re.sub(r'[^A-Za-z0-9_.=-]+', '_', query_name)}")
                    plt.show()
            """
        ),
        md("## Automatic Scenario Plots"),
        code(
            r"""
            # Latency / RTT / decomposition signals.
            plot_query(r"latency|rtt|same_packet|gap", title="Latency and RTT metrics")
            box_query(r"latency|rtt|same_packet|gap")

            # RAN/RF context: these are the main correlation signals for interference.
            plot_query(r"mcs|bler|harq|round|cqi|rsrp|rsrq|sinr|snr|prach|i0|noise|prb", title="RAN/RF metrics")

            # Throughput and resource context.
            plot_query(r"throughput|bitrate|bytes|packet|lost|drop", title="Throughput, packet, and loss metrics")
            """
        ),
        md("## Manual Exploration"),
        code(
            r"""
            # Change these patterns and rerun the cell for deeper dives.
            # Examples:
            # plot_query("gtp_teid_latency")
            # plot_query("mcs")
            # plot_query("bler")
            # plot_query("harq")
            # plot_query("prach")
            available_queries = sorted(prom["query_name"].dropna().unique()) if not prom.empty else []
            available_queries
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
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--scenario-root")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    scenario_root = Path(args.scenario_root) if args.scenario_root else results_dir / "by_window" / "scenario"
    if not scenario_root.exists():
        print(f"Scenario root not found: {scenario_root}")
        return 0

    count = 0
    for scenario_dir in sorted(path for path in scenario_root.iterdir() if path.is_dir()):
        scenario = scenario_dir.name
        notebook = build_notebook(safe_title(scenario))
        out = scenario_dir / "scenario_analysis.ipynb"
        out.write_text(json.dumps(notebook, indent=2), encoding="utf-8")
        count += 1
    print(f"Created {count} per-scenario notebooks in {scenario_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
