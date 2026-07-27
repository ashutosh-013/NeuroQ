"""
Basic statistical analysis for NeuroQ job outcomes.

This script reads the `job_outcomes` table from `neuroq_history.db`
and computes:
 - count, mean, std, and 95% confidence interval of success_metric
   per job_type (e.g. QAOA_Advisor, QAOA_Default, QAOA_Random)
 - a simple Welch t-test and Cohen's d between Advisor and Default

Usage (from this folder):
    python analysis_stats.py

Adjust BACKEND_NAME and JOB_TYPES as needed for your experiments.
"""

import math
import sqlite3
from typing import List

import numpy as np
import pandas as pd
from scipy import stats

DB_FILE = "neuroq_history.db"

# Adjust these to match how you log QAOA/VQE runs from your experiments.
BACKEND_NAME = "ibm_fezz"  # or any backend you are using
JOB_TYPES: List[str] = ["QAOA_Advisor", "QAOA_Default", "QAOA_Random"]


def load_data() -> pd.DataFrame:
    conn = sqlite3.connect(DB_FILE)
    try:
        df = pd.read_sql_query(
            """
            SELECT *
            FROM job_outcomes
            WHERE backend = ?
              AND job_type IN ({})
            """.format(
                ",".join("?" for _ in JOB_TYPES)
            ),
            conn,
            params=[BACKEND_NAME, *JOB_TYPES],
        )
    finally:
        conn.close()
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("No rows found for the specified backend and job_types.")

    grouped = df.groupby("job_type")["success_metric"].agg(["count", "mean", "std"])
    grouped["sem"] = grouped["std"] / np.sqrt(grouped["count"].clip(lower=1))
    grouped["ci95_low"] = grouped["mean"] - 1.96 * grouped["sem"]
    grouped["ci95_high"] = grouped["mean"] + 1.96 * grouped["sem"]
    return grouped


def welch_t_and_effect(
    df: pd.DataFrame,
    type_a: str = "QAOA_Advisor",
    type_b: str = "QAOA_Default",
) -> None:
    a = df[df["job_type"] == type_a]["success_metric"].to_numpy()
    b = df[df["job_type"] == type_b]["success_metric"].to_numpy()

    if len(a) < 2 or len(b) < 2:
        print(f"Not enough samples to compare {type_a} vs {type_b}.")
        return

    t_stat, p_val = stats.ttest_ind(a, b, equal_var=False)

    # Pooled standard deviation for Cohen's d
    var_a = a.var(ddof=1)
    var_b = b.var(ddof=1)
    pooled_sd = math.sqrt(0.5 * (var_a + var_b))
    d = (a.mean() - b.mean()) / pooled_sd if pooled_sd > 0 else float("nan")

    print(f"\nWelch t-test {type_a} vs {type_b}:")
    print(f"  mean({type_a}) = {a.mean():.3f}, n={len(a)}")
    print(f"  mean({type_b}) = {b.mean():.3f}, n={len(b)}")
    print(f"  t-statistic    = {t_stat:.3f}")
    print(f"  p-value        = {p_val:.4g}")
    print(f"  Cohen's d      = {d:.3f}")


def main() -> None:
    df = load_data()
    print(f"Loaded {len(df)} rows from job_outcomes for backend={BACKEND_NAME}.")

    summary = summarize(df)
    print("\nPer-condition summary (success_metric):")
    print(summary.to_string(float_format=lambda x: f"{x:.3f}"))

    if len(JOB_TYPES) >= 2:
        # By default, compare first two job types
        welch_t_and_effect(df, JOB_TYPES[0], JOB_TYPES[1])


if __name__ == "__main__":
    main()

