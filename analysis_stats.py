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

import argparse
import math
import sqlite3
from typing import List, Optional

import numpy as np
import pandas as pd
from scipy import stats

DB_FILE = "neuroq_history.db"

# Default backend and target job types
DEFAULT_BACKEND = "ibm_fez"
DEFAULT_JOB_TYPES: List[str] = ["manual", "Bell_counts", "QAOA_Advisor", "QAOA_Default"]


def get_available_backends() -> List[str]:
    """Retrieve distinct backends present in job_outcomes."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT backend FROM job_outcomes WHERE backend IS NOT NULL")
        rows = [r[0] for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def get_available_job_types(backend: str) -> List[str]:
    """Retrieve distinct job types present for a given backend."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT job_type FROM job_outcomes WHERE backend = ? AND job_type IS NOT NULL",
            (backend,),
        )
        rows = [r[0] for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def load_data(backend_name: str, job_types: Optional[List[str]] = None) -> pd.DataFrame:
    conn = sqlite3.connect(DB_FILE)
    try:
        if job_types:
            placeholders = ",".join("?" for _ in job_types)
            query = f"""
                SELECT *
                FROM job_outcomes
                WHERE backend = ?
                  AND job_type IN ({placeholders})
            """
            params = [backend_name, *job_types]
        else:
            query = """
                SELECT *
                FROM job_outcomes
                WHERE backend = ?
            """
            params = [backend_name]

        df = pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()
    return df


def summarize(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if df.empty:
        return None

    grouped = df.groupby("job_type")["success_metric"].agg(["count", "mean", "std"])
    grouped["sem"] = grouped["std"] / np.sqrt(grouped["count"].clip(lower=1))
    grouped["ci95_low"] = grouped["mean"] - 1.96 * grouped["sem"]
    grouped["ci95_high"] = grouped["mean"] + 1.96 * grouped["sem"]
    return grouped


def welch_t_and_effect(
    df: pd.DataFrame,
    type_a: str,
    type_b: str,
) -> None:
    a = df[df["job_type"] == type_a]["success_metric"].dropna().to_numpy()
    b = df[df["job_type"] == type_b]["success_metric"].dropna().to_numpy()

    if len(a) < 2 or len(b) < 2:
        print(f"\n[!] Not enough samples for Welch t-test ({type_a}: n={len(a)}, {type_b}: n={len(b)}; need >= 2 each).")
        return

    t_stat, p_val = stats.ttest_ind(a, b, equal_var=False)

    # Pooled standard deviation for Cohen's d
    var_a = a.var(ddof=1)
    var_b = b.var(ddof=1)
    pooled_sd = math.sqrt(0.5 * (var_a + var_b))
    d = (a.mean() - b.mean()) / pooled_sd if pooled_sd > 0 else float("nan")

    print(f"\nWelch t-test: {type_a} vs {type_b}:")
    print(f"  mean({type_a}) = {a.mean():.3f}, n={len(a)}")
    print(f"  mean({type_b}) = {b.mean():.3f}, n={len(b)}")
    print(f"  t-statistic    = {t_stat:.3f}")
    print(f"  p-value        = {p_val:.4g}")
    print(f"  Cohen's d      = {d:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Statistical analysis of NeuroQ job outcomes.")
    parser.add_argument("--backend", type=str, default=DEFAULT_BACKEND, help="Backend name (e.g., ibm_fez)")
    parser.add_argument("--types", nargs="*", default=None, help="Job types to compare (optional)")
    args = parser.parse_args()

    backend = args.backend
    available_backends = get_available_backends()
    if available_backends and backend not in available_backends:
        print(f"Note: '{backend}' not found in job_outcomes. Available: {available_backends}")
        backend = available_backends[0]
        print(f"Defaulting to backend: '{backend}'")

    available_types = get_available_job_types(backend)
    selected_types = args.types
    if selected_types:
        # Check intersection
        valid_selected = [t for t in selected_types if t in available_types]
        if not valid_selected:
            print(f"Requested job types {selected_types} not found for {backend}. Using all available: {available_types}")
            selected_types = available_types
        else:
            selected_types = valid_selected
    else:
        selected_types = available_types

    df = load_data(backend, selected_types)
    print(f"Loaded {len(df)} rows from job_outcomes for backend={backend}.")

    if df.empty:
        print(f"No job outcomes recorded yet for backend={backend}.")
        return

    summary = summarize(df)
    if summary is not None:
        print("\nPer-condition summary (success_metric):")
        print(summary.to_string(float_format=lambda x: f"{x:.3f}"))

    unique_types = list(df["job_type"].dropna().unique())
    if len(unique_types) >= 2:
        welch_t_and_effect(df, unique_types[0], unique_types[1])
    else:
        print(f"\nSingle condition recorded ({unique_types}). Log additional conditions to run Welch t-test.")


if __name__ == "__main__":
    main()

