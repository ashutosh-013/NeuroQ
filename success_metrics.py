import math
from typing import Any, Dict, Optional


def _safe_total(counts: Dict[str, int]) -> int:
    return int(sum(int(v) for v in counts.values()))


def bell_success_from_counts(counts: Dict[str, int]) -> float:
    """
    Success metric for Bell-state style tests:
      success = P(00) + P(11)
    """
    total = _safe_total(counts)
    if total <= 0:
        return 0.0
    return (counts.get("00", 0) + counts.get("11", 0)) / total


def zz_expectation_from_counts(counts: Dict[str, int]) -> float:
    """
    Compute <Z0 Z1> from 2-qubit computational basis counts:
      <ZZ> = P(00)+P(11) - P(01)-P(10)
    """
    total = _safe_total(counts)
    if total <= 0:
        return 0.0
    p00 = counts.get("00", 0) / total
    p01 = counts.get("01", 0) / total
    p10 = counts.get("10", 0) / total
    p11 = counts.get("11", 0) / total
    return (p00 + p11) - (p01 + p10)


def energy_success_metric(
    E_measured: float,
    E_ideal: float,
    tau: float,
) -> float:
    """
    Map an energy error to [0, 1]:
      success = max(0, 1 - |E_measured - E_ideal| / tau)
    """
    if tau <= 0 or math.isnan(tau):
        return 0.0
    if any(math.isnan(x) for x in (E_measured, E_ideal)):
        return 0.0
    return max(0.0, 1.0 - abs(E_measured - E_ideal) / tau)


def parse_counts_json(payload: Any) -> Optional[Dict[str, int]]:
    """
    Parse counts from a JSON-like payload (dict). Returns None if invalid.
    Accepts keys like "00","01","10","11" with int-like values.
    """
    if not isinstance(payload, dict):
        return None
    counts: Dict[str, int] = {}
    for k, v in payload.items():
        if not isinstance(k, str):
            continue
        try:
            counts[k] = int(v)
        except Exception:
            return None
    return counts

