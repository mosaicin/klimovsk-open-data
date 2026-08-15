#!/usr/bin/env python3
"""Calculate transparent price metrics for the verified OCD procurement sample."""
from pathlib import Path
import json
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "ocd_44fz_verified_sample.csv"
OUT = ROOT / "ocd_price_analysis"
OUT.mkdir(exist_ok=True)

df = pd.read_csv(INPUT)
for col in ["initial_price_rub", "final_price_rub"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")
df["reduction_rub"] = df["initial_price_rub"] - df["final_price_rub"]
df["reduction_pct"] = df["reduction_rub"] / df["initial_price_rub"] * 100
df["final_to_initial_pct"] = df["final_price_rub"] / df["initial_price_rub"] * 100
df["ocd_won"] = df["ocd_role"].fillna("").str.contains("Победитель", case=False, na=False) & ~df["ocd_role"].fillna("").str.contains("Не ОЦД", case=False, na=False)

valid = df.dropna(subset=["initial_price_rub", "final_price_rub"]).copy()
summary = {
    "source_file": INPUT.name,
    "rows_in_source": int(len(df)),
    "rows_with_both_prices": int(len(valid)),
    "unique_procurements": int(valid["procurement_id"].nunique()),
    "note": "This is the verified sample currently present in the repository, not a complete 232-contract export.",
    "reduction_pct": {
        "min": float(valid["reduction_pct"].min()),
        "median": float(valid["reduction_pct"].median()),
        "mean": float(valid["reduction_pct"].mean()),
        "max": float(valid["reduction_pct"].max()),
        "q1": float(valid["reduction_pct"].quantile(0.25)),
        "q3": float(valid["reduction_pct"].quantile(0.75)),
    },
    "contracts_marked_ocd_winner": int(valid["ocd_won"].sum()),
    "contracts_marked_not_ocd_or_other_winner": int((~valid["ocd_won"]).sum()),
}
valid.sort_values("reduction_pct", ascending=False).to_csv(OUT / "verified_sample_with_price_metrics.csv", index=False)
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
print("\nDetailed rows:")
print(valid[["procurement_id","year","customer","region","initial_price_rub","final_price_rub","reduction_rub","reduction_pct","ocd_role"]].to_string(index=False))

plot_df = valid.sort_values("reduction_pct", ascending=True).copy()
labels = [f"{row.year}\n{row.region}" for row in plot_df.itertuples()]
plt.figure(figsize=(10, 5.2), dpi=160)
plt.barh(labels, plot_df["reduction_pct"], color="#c49a4a")
plt.xlabel("Снижение от НМЦК, %")
plt.xlim(0, 100)
plt.title("Проверенная выборка ОЦД: снижение цены по закупкам")
plt.grid(axis="x", alpha=0.2)
plt.tight_layout()
plt.savefig(OUT / "reduction_by_procurement.png", bbox_inches="tight")
plt.close()
