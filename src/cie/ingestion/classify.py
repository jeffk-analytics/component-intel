# src/cie/ingestion/classify.py
"""Pilot-category classification, driven by Nexar's category taxonomy with
description-keyword fallback. Anything unmatched is UNCLASSIFIED — resolved
but out of pilot scope, never an error."""
from cie.models.enums import PilotCategory

# (category-path substring, description keywords) per pilot category.
_RULES: list[tuple[PilotCategory, tuple[str, ...], tuple[str, ...]]] = [
    (PilotCategory.MLCC,
     ("ceramic capacitor", "mlcc"),
     ("mlcc", "ceramic capacitor", "cap cer")),
    (PilotCategory.BUCK_CONVERTER,
     ("dc dc converter", "dc-dc converter", "dc dc converters",
      "switching regulator", "voltage regulator"),
     ("buck", "step-down", "step down", "synchronous buck",
      "conv dc/dc", "dc/dc", "dc dc")),
    (PilotCategory.MICROCONTROLLER,
     ("microcontroller",),
     ("mcu", "microcontroller")),
    (PilotCategory.CRYSTAL_OSCILLATOR,
     ("crystal", "oscillator"),
     ("crystal", "oscillator", "mhz", "khz")),
    (PilotCategory.CAN_TRANSCEIVER,
     ("can interface", "can transceiver", "transceiver", "drivers, receivers"),
     ("can transceiver", "can bus", "can fd", "txrx can", " can ", "canbus")),
]


def classify(
    category_path: str | None, category_name: str | None, description: str | None
) -> tuple[PilotCategory, str | None]:
    """Returns (category, category_source) — source names the evidence used."""
    path = (category_path or category_name or "").lower()
    desc = (description or "").lower()
    for cat, path_keys, desc_keys in _RULES:
        if any(k in path for k in path_keys):
            # Taxonomy hit still needs a description sanity check for the
            # two overlap-prone rules (regulators, transceivers).
            if cat in (PilotCategory.BUCK_CONVERTER, PilotCategory.CAN_TRANSCEIVER):
                if not any(k in desc or k in path for k in desc_keys):
                    continue
            return cat, f"nexar_taxonomy:{category_name or category_path}"
    for cat, _, desc_keys in _RULES:
        if any(k in desc for k in desc_keys):
            return cat, "description_keywords"
    return PilotCategory.UNCLASSIFIED, None
