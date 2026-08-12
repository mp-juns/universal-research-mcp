#!/usr/bin/env python3
"""Render the public development-only Integrity & Claim-Gating v1 figure."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "benchmarks/results/integrity-claim-gate-v1-development-20260813.json"
OUTPUT = ROOT / "docs/assets/integrity-claim-gate-v1-development-20260813.png"


def _bar_labels(axis: plt.Axes, values: list[float], labels: list[str]) -> None:
    for index, (value, label) in enumerate(zip(values, labels, strict=True)):
        axis.text(index, value + max(values) * 0.035, label, ha="center", va="bottom", fontweight="bold")


def main() -> None:
    data = json.loads(RESULT.read_text(encoding="utf-8"))
    names = ["Filesystem", "Manifest", "MCP evidence", "MCP + gate"]
    keys = ["filesystem", "filesystem_manifest", "mcp_evidence_only", "mcp_claim_gate"]
    conditions = [data["conditions"][key] for key in keys]
    colors = ["#59636e", "#8a96a3", "#3388a8", "#0e5b78"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    figure, axes = plt.subplots(1, 3, figsize=(19, 6.8))
    figure.subplots_adjust(left=0.045, right=0.965, top=0.82, bottom=0.20, wspace=0.13)

    unsafe = [item["unsafe_material_assertion_rate"] * 100 for item in conditions]
    axes[0].bar(names, unsafe, color=colors, width=0.68)
    axes[0].set_title("Unsafe material assertions\n(18 synthetic fault tasks)", fontweight="bold")
    axes[0].set_ylabel("Rate (%)")
    axes[0].set_ylim(0, 40)
    _bar_labels(axes[0], unsafe, [f"{value:.1f}%" for value in unsafe])

    coverage = [item["clean_supported_claim_coverage"] * 100 for item in conditions]
    axes[1].bar(names, coverage, color=colors, width=0.68)
    axes[1].set_title("Clean supported-claim coverage\n(6 answerable tasks)", fontweight="bold")
    axes[1].set_ylabel("Coverage (%)")
    axes[1].set_ylim(0, 115)
    _bar_labels(axes[1], coverage, [f"{value:.1f}%" for value in coverage])

    x = np.arange(4)
    token_k = [item["mean_provider_total_tokens"] / 1000 for item in conditions]
    latency = [item["mean_latency_ms"] / 1000 for item in conditions]
    width = 0.38
    bars_a = axes[2].bar(x - width / 2, token_k, width, label="Tokens (thousands)", color="#d8872d")
    axis_latency = axes[2].twinx()
    bars_b = axis_latency.bar(x + width / 2, latency, width, label="Latency (seconds)", color="#764c9d")
    axes[2].set_title("Measured execution burden\n(per task mean)", fontweight="bold")
    axes[2].set_ylabel("Provider-reported tokens (thousands)")
    axis_latency.set_ylabel("Wall latency (seconds)")
    axes[2].set_xticks(x, names)
    axes[2].bar_label(bars_a, labels=[f"{value:.0f}k" for value in token_k], padding=3, fontsize=9)
    axis_latency.bar_label(bars_b, labels=[f"{value:.1f}s" for value in latency], padding=3, fontsize=9)
    axes[2].legend([bars_a, bars_b], ["Tokens (thousands)", "Latency (seconds)"], loc="upper left", fontsize=9)

    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", alpha=0.22)
        axis.set_axisbelow(True)
        axis.tick_params(axis="x", labelrotation=14)
    axis_latency.spines[["top"]].set_visible(False)
    figure.suptitle("Universal Research MCP: Integrity & Claim-Gating v1 development run", fontsize=18, fontweight="bold")
    figure.text(
        0.5, 0.045,
        "Synthetic public development corpus; 24 tasks × 4 conditions × 1 run; gpt-5.6-terra, low reasoning. "
        "Condition-blinded model evaluation. The paired 95% interval includes zero: not confirmatory evidence.",
        ha="center", fontsize=9,
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(OUTPUT, dpi=220, bbox_inches="tight")


if __name__ == "__main__":
    main()
