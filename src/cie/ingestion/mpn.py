# src/cie/ingestion/mpn.py
"""MPN normalization and suffix/packaging-variant disambiguation.

Strategy: exact match wins; else prefer the candidate whose MPN is the
query plus a known packaging/qualifier suffix; else longest common prefix.
Every rejected candidate is recorded as an AlternateMatch — disambiguation
must be auditable, never silent."""
import re

from cie.ingestion.clients.common import SpineCandidate
from cie.models.part import AlternateMatch

# Common trailing packaging/qualifier suffixes (extend as pilot data demands).
_PACKAGING_SUFFIXES = (
    "TR", "-TR", "CT", "-CT", "T", "-T", "R", "-R", "TL", "-TL",
    "/NOPB", "-REEL", "REEL", "-ND", "+", "#PBF", "-E4", ",118", ",215",
)
_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize(mpn: str) -> str:
    return mpn.strip().upper()


def _stripped(mpn: str) -> str:
    return _NON_ALNUM.sub("", normalize(mpn))


def choose_candidate(
    query: str, candidates: list[SpineCandidate]
) -> tuple[SpineCandidate | None, list[AlternateMatch]]:
    """Pick the best candidate for the queried MPN.

    Returns (chosen, alternates). chosen is None only if candidates is empty.
    """
    if not candidates:
        return None, []
    q = normalize(query)
    qs = _stripped(query)

    def score(c: SpineCandidate) -> tuple[int, int]:
        m, ms = normalize(c.mpn), _stripped(c.mpn)
        if m == q or ms == qs:
            return (3, len(ms))                       # exact
        if ms.startswith(qs):
            rest = normalize(c.mpn)[len(q):] if normalize(c.mpn).startswith(q) else ""
            if rest and any(rest == s or rest == s.lstrip("-") for s in _PACKAGING_SUFFIXES):
                return (2, len(ms))                   # query + packaging suffix
            return (1, -len(ms))                      # extension; prefer shortest
        common = 0
        for a, b in zip(qs, ms):
            if a != b:
                break
            common += 1
        return (0, common)                            # prefix similarity only

    ranked = sorted(candidates, key=score, reverse=True)
    chosen = ranked[0]
    alternates = [
        AlternateMatch(
            mpn=c.mpn, manufacturer=c.manufacturer,
            note=_reason(score(c)),
        )
        for c in ranked[1:]
    ]
    return chosen, alternates


def _reason(s: tuple[int, int]) -> str:
    return {
        3: "exact-match duplicate (different manufacturer or listing)",
        2: "packaging/qualifier suffix variant",
        1: "longer suffix variant, not a known packaging code",
        0: "weaker prefix similarity",
    }[s[0]]
