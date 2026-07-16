# src/cie/substitution/fit.py
"""Fit checking: does candidate part B satisfy the sacred parameters of
reference part A? Verdicts per parameter: match / exceeds / MISMATCH /
unknown. Overall: FIT (all satisfied), UNVERIFIED (nothing violated but
something unknowable), REJECTED (a sacred parameter differs), or
POLICY_NO_AUTO (category where guessing is forbidden, e.g. MCUs).

Normalization notes, learned from real distributor data:
  * "10 µF" == "10uF" == "10 uF"  (micro sign, spacing)
  * "0805 (2012 Metric)" == "0805" (imperial name with metric suffix)
  * meet-or-exceed comparisons parse the number from the RAW string,
    case-sensitively, because 'm' (milli) and 'M' (mega) differ by 10^9.
"""
import json
import re
from enum import Enum
from functools import lru_cache
from pathlib import Path

_RULES_FILE = Path(__file__).parent / "fit_rules.json"

_NUM_UNIT = re.compile(r"(-?\d+(?:\.\d+)?)\s*([pnumkMG\u00b5\u03bc]?)")
_MULT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "\u00b5": 1e-6, "\u03bc": 1e-6,
         "m": 1e-3, "": 1.0, "k": 1e3, "M": 1e6, "G": 1e9}


class FitVerdict(str, Enum):
    FIT = "fit"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"
    POLICY_NO_AUTO = "policy_no_auto"


@lru_cache(maxsize=1)
def load_rules() -> dict:
    return json.loads(_RULES_FILE.read_text(encoding="utf-8"))


def rules_for(category_value: str) -> dict:
    cats = load_rules()["categories"]
    return cats.get(category_value, cats["unclassified"])


def norm_text(v: str) -> str:
    """Equality normalization: micro sign, case, spaces, metric suffix."""
    v = v.replace("\u00b5", "u").replace("\u03bc", "u").strip()
    v = re.sub(r"\s*\(.*?metric.*?\)\s*", "", v, flags=re.IGNORECASE)
    return re.sub(r"\s+", "", v).lower()


def parse_magnitude(raw: str) -> float | None:
    """'25V' -> 25.0; '10 uF' -> 1e-5; '24MHz' -> 2.4e7. None if unparseable.
    Case-sensitive prefix read happens BEFORE any lowercasing."""
    m = _NUM_UNIT.search(raw.strip())
    if not m:
        return None
    try:
        return float(m.group(1)) * _MULT[m.group(2)]
    except (KeyError, ValueError):
        return None


def check_fit(category_value: str, ref_params: dict[str, str],
              cand_params: dict[str, str]) -> tuple[FitVerdict, list[str], list[str]]:
    """Returns (verdict, evidence lines, unknown parameter names)."""
    rules = rules_for(category_value)
    if not rules.get("auto_substitution", False):
        return (FitVerdict.POLICY_NO_AUTO,
                [f"policy: {rules.get('policy_reason', 'no auto-substitution')}"],
                [])
    evidence: list[str] = []
    unknowns: list[str] = []
    rejected = False

    for param in rules.get("must_match", []):
        r, c = ref_params.get(param), cand_params.get(param)
        if r is None or c is None:
            unknowns.append(param)
            evidence.append(f"{param}: UNKNOWN (reference: {r!r}, "
                            f"candidate: {c!r}) — cannot verify")
        elif norm_text(r) == norm_text(c):
            evidence.append(f"{param}: match ('{r}')")
        else:
            rejected = True
            evidence.append(f"{param}: MISMATCH ('{r}' vs '{c}')")

    for param in rules.get("must_meet_or_exceed", []):
        r, c = ref_params.get(param), cand_params.get(param)
        if r is None or c is None:
            unknowns.append(param)
            evidence.append(f"{param}: UNKNOWN (reference: {r!r}, "
                            f"candidate: {c!r}) — cannot verify")
            continue
        rv, cv = parse_magnitude(r), parse_magnitude(c)
        if rv is None or cv is None:
            unknowns.append(param)
            evidence.append(f"{param}: UNPARSEABLE ('{r}' vs '{c}') — "
                            f"cannot verify")
        # tolerance: '10000nF' and '10uF' must compare as equal despite
        # floating-point arithmetic taking different paths to the value
        elif cv >= rv * (1 - 1e-9):
            word = "exceeds" if cv > rv * (1 + 1e-9) else "meets"
            evidence.append(f"{param}: {word} ('{c}' vs required '{r}')")
        else:
            rejected = True
            evidence.append(f"{param}: BELOW REQUIREMENT ('{c}' < '{r}')")

    if rejected:
        return FitVerdict.REJECTED, evidence, unknowns
    if unknowns:
        return FitVerdict.UNVERIFIED, evidence, unknowns
    return FitVerdict.FIT, evidence, unknowns
