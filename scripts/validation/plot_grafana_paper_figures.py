#!/usr/bin/env python3
"""Create Matplotlib paper figures from summarized Grafana CSV artifacts."""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ModuleNotFoundError as exc:  # pragma: no cover - user environment guard
    raise SystemExit(
        "matplotlib is required. Install it in a local environment with:\n"
        "  python3 -m venv .venv-paper\n"
        "  .venv-paper/bin/python -m pip install matplotlib pandas numpy\n"
        "Then rerun this script with .venv-paper/bin/python."
    ) from exc


PALETTE = {
    "gnb": "#2F6DB3",
    "upf": "#D05A3A",
    "ran": "#2F6DB3",
    "core": "#D05A3A",
    "cpu": "#4B8F6B",
    "memory": "#7A5AA6",
}

IMSI_TO_UE = {
    "001010000000006": "qhat01",
    "001010000000007": "qhat02",
    "001010000000008": "qhat03",
}

UE_COLORS = {
    "qhat01": "#1F77B4",
    "qhat02": "#D62728",
    "qhat03": "#2CA02C",
}

VALUE_RE = re.compile(r"[-+]?(?:\d+(?:,\d{3})*|\d*)(?:\.\d+)?(?:[eE][-+]?\d+)?")


def configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 130,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.04,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "font.family": "DejaVu Sans",
            "font.size": 8.5,
            "axes.labelsize": 9,
            "axes.titlesize": 9.5,
            "axes.titleweight": "bold",
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.24,
            "grid.linewidth": 0.7,
            "axes.axisbelow": True,
        }
    )


def save_figure(fig: plt.Figure, out_dir: Path, stem: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{stem}.pdf")
    fig.savefig(out_dir / f"{stem}.svg")
    fig.savefig(out_dir / f"{stem}.png")
    plt.close(fig)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"Missing required file: {path}")
    return pd.read_csv(path)


def read_grafana_csv(path: Path) -> pd.DataFrame:
    first = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[0]
    skiprows = 1 if first.strip().lower().startswith("sep=") else 0
    df = pd.read_csv(path, skiprows=skiprows, encoding="utf-8-sig")
    df["Time"] = pd.to_datetime(df["Time"], errors="coerce")
    return df


def find_grafana_export(csv_dir: Path, prefix: str) -> Path:
    matches = sorted(csv_dir.glob(f"{prefix}-*.csv"))
    if not matches:
        raise SystemExit(f"Missing Grafana export matching: {csv_dir}/{prefix}-*.csv")
    return matches[-1]


def parse_number(value: object) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return np.nan
    text = str(value).replace("\ufeff", "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return np.nan
    match = VALUE_RE.search(text.replace(" ", ""))
    if not match or match.group(0) in {"", "+", "-", "."}:
        return np.nan
    try:
        number = float(match.group(0).replace(",", ""))
    except ValueError:
        return np.nan

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


def filter_window(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["Time"] >= start) & (df["Time"] <= end)].copy()


def minutes_since_start(times: pd.Series, start: pd.Timestamp) -> pd.Series:
    return (times - start).dt.total_seconds() / 60.0


def empirical_cdf(values: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    clean = clean[np.isfinite(clean)]
    clean = clean[clean > 0]
    if clean.size == 0:
        return np.array([]), np.array([])
    clean = np.sort(clean)
    y = np.arange(1, clean.size + 1, dtype=float) / clean.size
    return clean, y


def empirical_ccdf(values: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    clean = clean[np.isfinite(clean)]
    clean = clean[clean > 0]
    if clean.size == 0:
        return np.array([]), np.array([])
    clean = np.sort(clean)
    y = (clean.size - np.arange(clean.size, dtype=float)) / clean.size
    return clean, y


def load_latency_timeseries(csv_dir: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for direction in ["Downlink", "Uplink"]:
        path = find_grafana_export(csv_dir, f"Mean Latency Per UE ({direction})")
        df = filter_window(read_grafana_csv(path), start, end)
        for col in df.columns:
            if col == "Time":
                continue
            parts = [part.strip() for part in str(col).split(" | ")]
            if len(parts) < 4:
                continue
            imsi, ue_ip, slice_id, probe_role = parts[:4]
            ue = IMSI_TO_UE.get(imsi)
            if not ue or slice_id != "01:ffffff":
                continue
            values = numeric_series(df[col])
            part = pd.DataFrame(
                {
                    "time": df["Time"],
                    "minutes": minutes_since_start(df["Time"], start),
                    "direction": direction.lower(),
                    "ue": ue,
                    "imsi": imsi,
                    "ue_ip": ue_ip,
                    "slice": slice_id,
                    "probe_role": probe_role,
                    "latency_ms": values,
                }
            ).dropna(subset=["latency_ms"])
            rows.append(part)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def load_same_packet_timeseries(csv_dir: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    metrics = [
        ("Mean Same-Packet UPF RTT Per UE", "upf_rtt_ms"),
        ("Mean Same-Packet gNB RTT Per UE", "gnb_rtt_ms"),
        ("Mean Same-Packet RTT Gap Per UE", "gap_ms"),
        ("Same-Packet Pair Rate Per UE", "pair_rate_hz"),
    ]
    for prefix, metric in metrics:
        path = find_grafana_export(csv_dir, prefix)
        df = filter_window(read_grafana_csv(path), start, end)
        for col in df.columns:
            if col == "Time":
                continue
            parts = [part.strip() for part in str(col).split(" | ")]
            if len(parts) < 6:
                continue
            imsi, ue_ip, slice_id, direction, probe_role, mode = parts[:6]
            ue = IMSI_TO_UE.get(imsi)
            if not ue or slice_id != "01:ffffff" or mode != "tcp":
                continue
            values = numeric_series(df[col])
            part = pd.DataFrame(
                {
                    "time": df["Time"],
                    "minutes": minutes_since_start(df["Time"], start),
                    "direction": direction,
                    "ue": ue,
                    "imsi": imsi,
                    "ue_ip": ue_ip,
                    "slice": slice_id,
                    "probe_role": probe_role,
                    "metric": metric,
                    "value": values,
                }
            ).dropna(subset=["value"])
            rows.append(part)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def plot_smoothed_line(
    ax: plt.Axes,
    data: pd.DataFrame,
    value_col: str,
    *,
    label: str,
    color: str,
    window: int = 5,
) -> None:
    if data.empty:
        return
    data = data.sort_values("minutes")
    ax.plot(data["minutes"], data[value_col], color=color, alpha=0.18, linewidth=0.6)
    smooth = data[value_col].rolling(window=window, min_periods=1, center=True).mean()
    ax.plot(data["minutes"], smooth, color=color, linewidth=1.8, label=label)


def ue_direction_label(row: pd.Series) -> str:
    direction = str(row["direction"]).capitalize()
    return f"{row['ue']}\n{direction}"


def grouped_probe_bar(
    ax: plt.Axes,
    data: pd.DataFrame,
    value_col: str,
    *,
    ylabel: str,
    title: str,
    ylim_pad: float = 1.18,
) -> None:
    data = data.copy()
    data["label"] = data.apply(ue_direction_label, axis=1)
    order = [
        f"{ue}\n{direction}"
        for ue in ["qhat01", "qhat02", "qhat03"]
        for direction in ["Downlink", "Uplink"]
        if f"{ue}\n{direction}" in set(data["label"])
    ]
    probes = [probe for probe in ["gnb", "upf"] if probe in set(data["probe_role"])]
    x = np.arange(len(order))
    width = 0.34 if len(probes) > 1 else 0.52

    for idx, probe in enumerate(probes):
        values = []
        for label in order:
            match = data[(data["label"] == label) & (data["probe_role"] == probe)]
            values.append(float(match[value_col].iloc[0]) if not match.empty else np.nan)
        offset = (idx - (len(probes) - 1) / 2) * width
        ax.bar(
            x + offset,
            values,
            width=width,
            color=PALETTE.get(probe, "#6B7280"),
            edgecolor="white",
            linewidth=0.7,
            label=probe.upper(),
        )

    ymax = np.nanmax(data[value_col].to_numpy(dtype=float))
    ax.set_ylim(0, ymax * ylim_pad if np.isfinite(ymax) and ymax > 0 else 1)
    ax.set_xticks(x)
    ax.set_xticklabels(order)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(frameon=False, ncol=max(1, len(probes)), loc="upper right")


def plot_latency_panels(summary_dir: Path, out_dir: Path) -> None:
    df = read_csv(summary_dir / "latency_per_ue_timeavg.csv")
    df = df[(df["ue"].astype(str).str.startswith("qhat")) & (df["slice"] == "01:ffffff")].copy()

    fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.55), constrained_layout=True)
    grouped_probe_bar(
        axes[0],
        df,
        "mean_latency",
        ylabel="Mean latency (ms)",
        title="Mean latency",
    )
    grouped_probe_bar(
        axes[1],
        df,
        "p95_latency",
        ylabel="P95 latency (ms)",
        title="Tail latency",
    )
    fig.suptitle("Latency decomposition by UE and direction", fontsize=10.5, fontweight="bold")
    save_figure(fig, out_dir, "paper_latency_mean_p95")

    fig, ax = plt.subplots(figsize=(6.1, 2.55), constrained_layout=True)
    grouped_probe_bar(
        ax,
        df,
        "latency_event_rate",
        ylabel="Event rate (events/s)",
        title="Observed latency event rate",
    )
    save_figure(fig, out_dir, "paper_latency_event_rate")


def plot_same_packet(summary_dir: Path, out_dir: Path) -> None:
    df = read_csv(summary_dir / "same_packet_timeavg.csv")
    df = df[(df["ue"].astype(str).str.startswith("qhat")) & (df["slice"] == "01:ffffff")].copy()
    df["label"] = df.apply(ue_direction_label, axis=1)
    order = [
        f"{ue}\n{direction}"
        for ue in ["qhat01", "qhat02", "qhat03"]
        for direction in ["Downlink", "Uplink"]
        if f"{ue}\n{direction}" in set(df["label"])
    ]
    x = np.arange(len(order))

    fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.55), constrained_layout=True)
    ax = axes[0]
    width = 0.34
    metrics = [
        ("mean_same_packet_gnb_rtt", "gNB RTT", PALETTE["gnb"]),
        ("mean_same_packet_upf_rtt", "UPF RTT", PALETTE["upf"]),
    ]
    for idx, (col, label, color) in enumerate(metrics):
        values = [float(df.loc[df["label"] == item, col].iloc[0]) for item in order]
        ax.bar(
            x + (idx - 0.5) * width,
            values,
            width=width,
            label=label,
            color=color,
            edgecolor="white",
            linewidth=0.7,
        )
    ax.set_xticks(x)
    ax.set_xticklabels(order)
    ax.set_ylabel("RTT (ms)")
    ax.set_title("Same-packet RTT")
    ax.legend(frameon=False)

    ax = axes[1]
    pair_rate = [float(df.loc[df["label"] == item, "same_packet_pair_rate"].iloc[0]) for item in order]
    ax.bar(x, pair_rate, color="#4B8F6B", edgecolor="white", linewidth=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(order)
    ax.set_ylabel("Pairs/s")
    ax.set_title("Pairing density")

    fig.suptitle("Same-packet validation signal", fontsize=10.5, fontweight="bold")
    save_figure(fig, out_dir, "paper_same_packet_validation")


def plot_system_overhead(summary_dir: Path, out_dir: Path) -> None:
    df = read_csv(summary_dir / "system_overhead_summary.csv")
    df = df[
        df["component"].isin(["metrics_parser", "packet-pairer", "ebpf-latency-probe"])
        & df["mean"].notna()
    ].copy()
    if df.empty:
        return

    components = ["metrics_parser", "packet-pairer", "ebpf-latency-probe"]
    components = [item for item in components if item in set(df["component"])]
    x = np.arange(len(components))

    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.35), constrained_layout=True)
    for ax, metric, ylabel, color in [
        (axes[0], "cpu_usage", "CPU usage (%)", PALETTE["cpu"]),
        (axes[1], "memory_usage", "Memory usage (%)", PALETTE["memory"]),
    ]:
        subset = df[df["metric"] == metric].set_index("component")
        values = [float(subset["mean"].get(component, np.nan)) for component in components]
        ax.bar(x, values, color=color, edgecolor="white", linewidth=0.7)
        ax.set_xticks(x)
        ax.set_xticklabels([item.replace("-", "\n") for item in components])
        ax.set_ylabel(ylabel)
        ax.set_title(ylabel.split(" (")[0])
    fig.suptitle("Monitoring component overhead", fontsize=10.5, fontweight="bold")
    save_figure(fig, out_dir, "paper_monitoring_overhead")


def plot_radio_context(summary_dir: Path, out_dir: Path) -> None:
    df = read_csv(summary_dir / "radio_rnti_summary.csv")
    keep = df[
        df["metric"].isin(
            [
                "snr_per_rnti",
                "downlink_mcs_per_rnti",
                "uplink_mcs_per_rnti",
                "downlink_bler_per_rnti",
                "uplink_bler_per_rnti",
            ]
        )
    ].copy()
    if keep.empty:
        return

    label_map = {
        "snr_per_rnti": "SNR (dB)",
        "downlink_mcs_per_rnti": "DL MCS",
        "uplink_mcs_per_rnti": "UL MCS",
        "downlink_bler_per_rnti": "DL BLER (%)",
        "uplink_bler_per_rnti": "UL BLER (%)",
    }
    keep["label"] = keep["metric"].map(label_map)
    keep = keep.dropna(subset=["label"]).set_index("label")
    labels = [label for label in label_map.values() if label in keep.index]
    means = [float(keep.loc[label, "mean"]) for label in labels]
    lows = [float(keep.loc[label, "p05"]) for label in labels]
    highs = [float(keep.loc[label, "p95"]) for label in labels]
    lower_err = np.array(means) - np.array(lows)
    upper_err = np.array(highs) - np.array(means)

    fig, ax = plt.subplots(figsize=(6.3, 2.55), constrained_layout=True)
    x = np.arange(len(labels))
    ax.bar(x, means, color="#6B7280", edgecolor="white", linewidth=0.7)
    ax.errorbar(
        x,
        means,
        yerr=np.vstack([lower_err, upper_err]),
        fmt="none",
        ecolor="#202733",
        elinewidth=0.9,
        capsize=3,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Mean with P05-P95 range")
    ax.set_title("RNTI-level radio context")
    save_figure(fig, out_dir, "paper_radio_context")


def plot_v2_latency_moving_timeseries(
    csv_dir: Path,
    out_dir: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    df = load_latency_timeseries(csv_dir, start, end)
    if df.empty:
        return df

    fig, axes = plt.subplots(2, 2, figsize=(7.3, 4.3), sharex=True, constrained_layout=True)
    panels = [
        ("downlink", "gnb", "Downlink, gNB probe"),
        ("downlink", "upf", "Downlink, UPF probe"),
        ("uplink", "gnb", "Uplink, gNB probe"),
        ("uplink", "upf", "Uplink, UPF probe"),
    ]
    for ax, (direction, probe, title) in zip(axes.flat, panels, strict=True):
        subset = df[(df["direction"] == direction) & (df["probe_role"] == probe)]
        for ue in ["qhat01", "qhat02", "qhat03"]:
            line = subset[subset["ue"] == ue]
            plot_smoothed_line(
                ax,
                line,
                "latency_ms",
                label=ue,
                color=UE_COLORS[ue],
                window=5,
            )
        ax.set_title(title)
        ax.set_ylabel("Latency (ms)")
        ax.set_xlim(0, (end - start).total_seconds() / 60.0)
        ax.legend(frameon=False, loc="upper right")
    axes[-1, 0].set_xlabel("Minutes since experiment start")
    axes[-1, 1].set_xlabel("Minutes since experiment start")
    fig.suptitle("Latency evolution over the run (thin=15 s samples, thick=5-sample moving mean)")
    save_figure(fig, out_dir, "paper_v2_latency_moving_timeseries")
    return df


def plot_v2_latency_cdf(latency_df: pd.DataFrame, out_dir: Path) -> None:
    if latency_df.empty:
        return

    panels = [
        ("downlink", "gnb", "Downlink, gNB probe"),
        ("downlink", "upf", "Downlink, UPF probe"),
        ("uplink", "gnb", "Uplink, gNB probe"),
        ("uplink", "upf", "Uplink, UPF probe"),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(7.3, 4.3), constrained_layout=True)
    for ax, (direction, probe, title) in zip(axes.flat, panels, strict=True):
        subset = latency_df[(latency_df["direction"] == direction) & (latency_df["probe_role"] == probe)]
        for ue in ["qhat01", "qhat02", "qhat03"]:
            x, y = empirical_cdf(subset.loc[subset["ue"] == ue, "latency_ms"])
            if x.size == 0:
                continue
            ax.plot(x, y, color=UE_COLORS[ue], linewidth=1.8, label=ue)
        ax.set_title(title)
        ax.set_xlabel("Latency (ms)")
        ax.set_ylabel("CDF")
        ax.set_ylim(0, 1.02)
        ax.legend(frameon=False, loc="lower right")
    fig.suptitle("Latency CDF from Grafana 15 s window samples")
    save_figure(fig, out_dir, "paper_v2_latency_cdf")

    fig, axes = plt.subplots(2, 2, figsize=(7.3, 4.3), constrained_layout=True)
    for ax, (direction, probe, title) in zip(axes.flat, panels, strict=True):
        subset = latency_df[(latency_df["direction"] == direction) & (latency_df["probe_role"] == probe)]
        for ue in ["qhat01", "qhat02", "qhat03"]:
            x, y = empirical_ccdf(subset.loc[subset["ue"] == ue, "latency_ms"])
            if x.size == 0:
                continue
            ax.semilogy(x, y, color=UE_COLORS[ue], linewidth=1.8, label=ue)
        ax.set_title(title)
        ax.set_xlabel("Latency (ms)")
        ax.set_ylabel("CCDF")
        ax.set_ylim(1e-2, 1.05)
        ax.legend(frameon=False, loc="upper right")
    fig.suptitle("Latency tail distribution from Grafana 15 s window samples")
    save_figure(fig, out_dir, "paper_v2_latency_tail_ccdf")


def plot_v2_latency_gap_timeseries(
    latency_df: pd.DataFrame,
    out_dir: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> None:
    if latency_df.empty:
        return

    fig, axes = plt.subplots(1, 2, figsize=(7.3, 2.75), sharex=True, constrained_layout=True)
    for ax, direction in zip(axes, ["downlink", "uplink"], strict=True):
        for ue in ["qhat01", "qhat02", "qhat03"]:
            subset = latency_df[(latency_df["direction"] == direction) & (latency_df["ue"] == ue)]
            pivot = subset.pivot_table(index=["time", "minutes"], columns="probe_role", values="latency_ms")
            if "upf" not in pivot or "gnb" not in pivot:
                continue
            gap = (pivot["upf"] - pivot["gnb"]).reset_index(name="gap_ms").dropna()
            plot_smoothed_line(
                ax,
                gap,
                "gap_ms",
                label=ue,
                color=UE_COLORS[ue],
                window=5,
            )
        ax.axhline(0, color="#202733", linewidth=0.8)
        ax.set_title(f"{direction.capitalize()} UPF-gNB latency gap")
        ax.set_ylabel("Latency gap (ms)")
        ax.set_xlabel("Minutes since experiment start")
        ax.set_xlim(0, (end - start).total_seconds() / 60.0)
        ax.legend(frameon=False, loc="upper right")
    fig.suptitle("Latency decomposition gap over time")
    save_figure(fig, out_dir, "paper_v2_latency_gap_timeseries")


def plot_v2_same_packet(csv_dir: Path, out_dir: Path, start: pd.Timestamp, end: pd.Timestamp) -> None:
    df = load_same_packet_timeseries(csv_dir, start, end)
    if df.empty:
        return

    fig, axes = plt.subplots(2, 2, figsize=(7.3, 4.3), sharex=True, constrained_layout=True)
    panels = [
        ("downlink", "gap_ms", "Downlink RTT gap", "Gap (ms)"),
        ("uplink", "gap_ms", "Uplink RTT gap", "Gap (ms)"),
        ("downlink", "pair_rate_hz", "Downlink pair rate", "Pairs/s"),
        ("uplink", "pair_rate_hz", "Uplink pair rate", "Pairs/s"),
    ]
    for ax, (direction, metric, title, ylabel) in zip(axes.flat, panels, strict=True):
        subset = df[(df["direction"] == direction) & (df["metric"] == metric)]
        for ue in ["qhat01", "qhat02", "qhat03"]:
            line = subset[subset["ue"] == ue].sort_values("minutes")
            plot_smoothed_line(
                ax,
                line,
                "value",
                label=ue,
                color=UE_COLORS[ue],
                window=5,
            )
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_xlim(0, (end - start).total_seconds() / 60.0)
        ax.legend(frameon=False, loc="upper right")
    axes[-1, 0].set_xlabel("Minutes since experiment start")
    axes[-1, 1].set_xlabel("Minutes since experiment start")
    fig.suptitle("Same-packet validation over time")
    save_figure(fig, out_dir, "paper_v2_same_packet_gap_pair_rate")

    fig, axes = plt.subplots(1, 2, figsize=(7.3, 2.85), constrained_layout=True)
    metric_styles = {
        "gnb_rtt_ms": ("gNB RTT", "-"),
        "upf_rtt_ms": ("UPF RTT", "--"),
    }
    for ax, direction in zip(axes, ["downlink", "uplink"], strict=True):
        subset = df[df["direction"] == direction]
        for ue in ["qhat01", "qhat02", "qhat03"]:
            for metric, (metric_label, linestyle) in metric_styles.items():
                x, y = empirical_cdf(subset.loc[(subset["ue"] == ue) & (subset["metric"] == metric), "value"])
                if x.size == 0:
                    continue
                ax.plot(
                    x,
                    y,
                    color=UE_COLORS[ue],
                    linestyle=linestyle,
                    linewidth=1.7,
                    label=f"{ue} {metric_label}",
                )
        ax.set_title(f"{direction.capitalize()} same-packet RTT CDF")
        ax.set_xlabel("RTT (ms)")
        ax.set_ylabel("CDF")
        ax.set_ylim(0, 1.02)
        ax.legend(frameon=False, fontsize=6.7, loc="lower right")
    fig.suptitle("Same-packet RTT distributions")
    save_figure(fig, out_dir, "paper_v2_same_packet_rtt_cdf")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--summary-dir",
        default="paper_artifacts/grafana_2026-05-01_1540_1648",
        help="Directory produced by summarize_grafana_csv_exports.py",
    )
    parser.add_argument(
        "--csv-dir",
        default="CSVGrafana",
        help="Directory containing raw Grafana CSV exports",
    )
    parser.add_argument("--start", default="2026-05-01 15:40:00")
    parser.add_argument("--end", default="2026-05-01 16:48:00")
    parser.add_argument(
        "--out-dir",
        default="paper_artifacts/grafana_2026-05-01_1540_1648/matplotlib_figures",
        help="Output directory for PDF/SVG/PNG figures",
    )
    args = parser.parse_args()

    configure_matplotlib()
    summary_dir = Path(args.summary_dir)
    csv_dir = Path(args.csv_dir)
    out_dir = Path(args.out_dir)
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    plot_latency_panels(summary_dir, out_dir)
    plot_same_packet(summary_dir, out_dir)
    plot_system_overhead(summary_dir, out_dir)
    plot_radio_context(summary_dir, out_dir)
    latency_df = plot_v2_latency_moving_timeseries(csv_dir, out_dir, start, end)
    plot_v2_latency_cdf(latency_df, out_dir)
    plot_v2_latency_gap_timeseries(latency_df, out_dir, start, end)
    plot_v2_same_packet(csv_dir, out_dir, start, end)

    print(f"Wrote Matplotlib paper figures to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
