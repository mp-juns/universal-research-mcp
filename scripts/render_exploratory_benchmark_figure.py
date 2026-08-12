#!/usr/bin/env python3
"""Render the public exploratory-diagnostic figure from published results."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "benchmarks/results/codex-directional-v1.json"
OUTPUT = ROOT / "docs/assets/exploratory-claim-safety-diagnostic-v1.png"


def _labels(axis: plt.Axes, values: list[float], formatter: str) -> None:
    maximum = max(values)
    for index, value in enumerate(values):
        axis.text(
            index,
            value + maximum * 0.035,
            formatter.format(value),
            ha="center",
            va="bottom",
            fontsize=11,
            fontweight="bold",
        )


def main() -> None:
    data = json.loads(RESULT.read_text(encoding="utf-8"))
    filesystem = data["ordinary_tasks"]["filesystem"]
    mcp = data["ordinary_tasks"]["mcp"]

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12})
    figure, axes = plt.subplots(1, 3, figsize=(19, 6.6), constrained_layout=True)
    colors = ["#566573", "#176B87"]
    conditions = ["Direct filesystem", "Universal Research MCP"]

    input_tokens = [filesystem["input_tokens"], mcp["input_tokens"]]
    axes[0].bar(conditions, input_tokens, color=colors, width=0.62)
    axes[0].set_title("Ordinary source tasks (4)", fontweight="bold")
    axes[0].set_ylabel("Host-reported input tokens")
    axes[0].set_ylim(0, max(input_tokens) * 1.22)
    axes[0].tick_params(axis="x", labelrotation=13)
    _labels(axes[0], input_tokens, "{:.0f}")
    axes[0].text(
        0.5,
        0.93,
        "Both: 4/4 factual answer + line citation",
        transform=axes[0].transAxes,
        ha="center",
        va="top",
        fontsize=10,
    )

    latency_seconds = [filesystem["latency_ms"] / 1000, mcp["latency_ms"] / 1000]
    axes[1].bar(conditions, latency_seconds, color=colors, width=0.62)
    axes[1].set_title("Ordinary source tasks (4)", fontweight="bold")
    axes[1].set_ylabel("Wall latency (seconds)")
    axes[1].set_ylim(0, max(latency_seconds) * 1.22)
    axes[1].tick_params(axis="x", labelrotation=13)
    _labels(axes[1], latency_seconds, "{:.1f}s")
    axes[1].text(
        0.5,
        0.93,
        "Measured overhead: 2.26× input, 2.02× latency",
        transform=axes[1].transAxes,
        ha="center",
        va="top",
        fontsize=10,
    )

    x = np.arange(2)
    width = 0.35
    direct = [6, 0]
    gated = [0, 6]
    axes[2].bar(x - width / 2, direct, width, label="Direct filesystem", color=colors[0])
    axes[2].bar(x + width / 2, gated, width, label="Universal Research MCP", color=colors[1])
    axes[2].set_title("Post-index source mutation (6 trials)", fontweight="bold")
    axes[2].set_ylabel("Trials")
    axes[2].set_xticks(x, ["Changed source accepted\nas verified evidence", "Correct abstention"])
    axes[2].set_ylim(0, 7)
    axes[2].set_yticks(range(0, 7))
    axes[2].legend(loc="upper center", fontsize=10)
    for bars in axes[2].containers:
        axes[2].bar_label(bars, padding=3, fontweight="bold")

    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", alpha=0.25)
        axis.set_axisbelow(True)

    figure.suptitle(
        "Universal Research MCP: exploratory measured claim-safety diagnostics",
        fontsize=19,
        fontweight="bold",
    )
    figure.text(
        0.5,
        0.005,
        "Synthetic sources; gpt-5.6-terra, low reasoning effort; one run per condition. "
        "Exploratory diagnostics, not a confirmatory benchmark or a general quality claim.",
        ha="center",
        fontsize=10,
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(OUTPUT, dpi=220, bbox_inches="tight")


if __name__ == "__main__":
    main()
