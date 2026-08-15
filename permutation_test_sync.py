#!/usr/bin/env python3
"""Permutation test for temporal synchrony of bids within procurement procedures.

Input CSV columns:
  procurement_id, participant_id, submitted_at
submitted_at must be parseable by pandas.to_datetime.

The test preserves the pooled timestamps and the number of observations per
participant, then randomly permutes participant labels within each procurement.
It tests whether cross-participant events fall unusually close in time.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import numpy as np
import pandas as pd


def cross_pair_times(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return timestamp differences and whether pairs have different participants."""
    frame = frame.sort_values("submitted_at").reset_index(drop=True)
    times = frame["submitted_at"].astype("int64").to_numpy() / 1_000_000_000
    participants = frame["participant_id"].astype(str).to_numpy()
    diffs = []
    cross = []
    for i in range(len(frame)):
        for j in range(i + 1, len(frame)):
            diffs.append(abs(times[j] - times[i]))
            cross.append(participants[i] != participants[j])
    return np.asarray(diffs, dtype=float), np.asarray(cross, dtype=bool)


def statistic(frame: pd.DataFrame, delta_seconds: float) -> dict[str, float]:
    """Calculate cross-participant near-event fraction and minimum gap."""
    diffs, cross = cross_pair_times(frame)
    cross_diffs = diffs[cross]
    if len(cross_diffs) == 0:
        return {"near_fraction": float("nan"), "min_gap_seconds": float("nan"), "cross_pairs": 0}
    return {
        "near_fraction": float(np.mean(cross_diffs <= delta_seconds)),
        "min_gap_seconds": float(np.min(cross_diffs)),
        "cross_pairs": int(len(cross_diffs)),
    }


def permuted_statistic(frame: pd.DataFrame, delta_seconds: float, rng: np.random.Generator) -> dict[str, float]:
    """Shuffle participant labels while preserving per-participant counts."""
    shuffled = frame.copy()
    shuffled["participant_id"] = rng.permutation(shuffled["participant_id"].to_numpy())
    return statistic(shuffled, delta_seconds)


def run_one(frame: pd.DataFrame, delta_seconds: float, permutations: int, seed: int) -> dict[str, object]:
    observed = statistic(frame, delta_seconds)
    if not np.isfinite(observed["near_fraction"]):
        return {"observed": observed, "permutations": 0, "p_value": None, "null_mean": None, "null_sd": None}
    rng = np.random.default_rng(seed)
    null = np.array([
        permuted_statistic(frame, delta_seconds, rng)["near_fraction"]
        for _ in range(permutations)
    ], dtype=float)
    # Add-one correction avoids zero p-values in finite Monte Carlo samples.
    p_value = (1.0 + float(np.sum(null >= observed["near_fraction"]))) / (permutations + 1.0)
    return {
        "observed": observed,
        "permutations": int(permutations),
        "p_value_upper_tail": float(p_value),
        "null_mean": float(np.mean(null)),
        "null_sd": float(np.std(null, ddof=1)) if len(null) > 1 else 0.0,
        "null_q025": float(np.quantile(null, 0.025)),
        "null_q975": float(np.quantile(null, 0.975)),
    }


def load_input(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"procurement_id", "participant_id", "submitted_at"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    df = df.copy()
    df["submitted_at"] = pd.to_datetime(df["submitted_at"], utc=True, errors="coerce")
    df = df.dropna(subset=["procurement_id", "participant_id", "submitted_at"])
    if df.empty:
        raise ValueError("No valid rows remain after timestamp parsing")
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="CSV containing procurement_id, participant_id, submitted_at")
    parser.add_argument("--delta-seconds", type=float, default=5.0)
    parser.add_argument("--permutations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260815)
    parser.add_argument("--out", type=Path, default=Path("permutation_sync_results.json"))
    args = parser.parse_args()
    if args.delta_seconds <= 0 or args.permutations < 100:
        parser.error("delta-seconds must be positive and permutations must be at least 100")

    df = load_input(args.input)
    results = []
    for procurement_id, group in df.groupby("procurement_id", sort=True):
        result = run_one(group, args.delta_seconds, args.permutations, args.seed)
        result["procurement_id"] = str(procurement_id)
        result["rows"] = int(len(group))
        result["participants"] = int(group["participant_id"].nunique())
        results.append(result)

    output = {
        "input": str(args.input),
        "delta_seconds": args.delta_seconds,
        "permutations": args.permutations,
        "seed": args.seed,
        "null_model": "shuffle participant labels within procurement; preserve pooled timestamps and participant counts",
        "results": results,
        "warning": "A small p-value is an unusual-timing indicator under this null model, not proof of an agreement or cartel.",
    }
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
