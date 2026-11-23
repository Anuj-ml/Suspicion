"""Band decision. FP costs 10x FN: one weak signal never reaches High."""

from .checks import Signal

# Deliberately ordinal, not a probability. A calibrated percentage is not
# defensible at 150-300 real labels (ponytail: no pretend precision).
BANDS = ("Low", "Elevated", "High")


def band(signals: list[Signal]) -> str:
    hard = any(s.severity == "hard" for s in signals)
    heuristics = {s.code for s in signals if s.severity == "heuristic"}
    if hard or len(heuristics) >= 2:
        return "High"
    if heuristics:
        return "Elevated"
    return "Low"
