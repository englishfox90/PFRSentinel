#!/usr/bin/env python3
"""Read a frame's Jev audit verdict against its human label — pure, Qt-free.

`jev_label_audit.py` stores TypeSafe Jev's answers under `jev_audit` in each
calibration JSON. Jev never saw the frame: it judged the metadata (image
statistics, exposure, moon, weather, NINA roof state). So its verdict is not
a label source — it never pre-fills the form and never enters `roof_votes` —
it is a second opinion that says "the numbers for this frame don't look like
the label you gave it". The labeling tool shows that beside the frame and the
Review tab filters on it, so the disagreements can be worked through by hand.

A conflict is *corroborated* when every other roof or sky source on the frame
(NINA, the vision AI) sides with Jev against the human label. Those come first:
they are the likeliest label errors.
"""
from .label_suggestion import SKY_LEVELS, to_bool

JEV_KEY = "jev_audit"


def make_jev_block(answers: dict, model: str, audited_at: str,
                   nina_in_state: bool, weather_in_state: bool) -> dict:
    """The block the audit script stores. Missing answers leave their fields out."""
    block = {
        "model": model,
        "audited_at": audited_at,
        "nina_in_state": bool(nina_in_state),
        "weather_in_state": bool(weather_in_state),
    }
    roof = answers.get("roof") or {}
    if roof.get("choice") in ("open", "closed"):
        probs = roof.get("probabilities") or {}
        block["roof_open"] = roof["choice"] == "open"
        block["roof_p_open"] = round(float(probs.get("open", 1.0 if block["roof_open"] else 0.0)), 3)
        block["roof_confidence"] = round(float(roof.get("confidence", 0.0)), 3)
    sky = answers.get("sky") or {}
    if sky.get("choice") in SKY_LEVELS:
        block["sky_condition"] = sky["choice"]
        block["sky_confidence"] = round(float(sky.get("confidence", 0.0)), 3)
        block["sky_probabilities"] = {k: round(float(v), 3)
                                      for k, v in (sky.get("probabilities") or {}).items()}
    stars = answers.get("stars") or {}
    if isinstance(stars.get("noul"), (int, float)):
        block["stars_p"] = round(float(stars["noul"]), 3)
    return block


def jev_block(cal: dict):
    block = cal.get(JEV_KEY)
    return block if isinstance(block, dict) and block else None


def roof_conflict(cal: dict):
    """(human, jev) when both exist and differ, else None."""
    jev = jev_block(cal)
    labels = cal.get("labels") or {}
    if not jev or "roof_open" not in jev or not labels.get("labeled_at"):
        return None
    human, verdict = to_bool(labels.get("roof_open")), bool(jev["roof_open"])
    return (human, verdict) if human != verdict else None


def sky_conflict(cal: dict):
    """(human, jev) when the frame has a human sky label and Jev picked another level."""
    jev = jev_block(cal)
    labels = cal.get("labels") or {}
    human = labels.get("sky_condition") or ""
    if not jev or not jev.get("sky_condition") or not labels.get("labeled_at"):
        return None
    if human not in SKY_LEVELS or not to_bool(labels.get("roof_open")):
        return None
    return (human, jev["sky_condition"]) if human != jev["sky_condition"] else None


def _other_roof_opinions(cal: dict) -> list:
    opinions = []
    rs = cal.get("roof_state") or {}
    if to_bool(rs.get("available")) and rs.get("roof_open") is not None:
        opinions.append(to_bool(rs["roof_open"]))
    ai = cal.get("ai_suggestion") or {}
    if ai.get("roof_open") is not None:
        opinions.append(to_bool(ai["roof_open"]))
    return opinions


def conflict_kinds(cal: dict) -> list:
    """Which of 'roof', 'sky' Jev disputes on this frame, in that order."""
    kinds = []
    if roof_conflict(cal):
        kinds.append("roof")
    if sky_conflict(cal):
        kinds.append("sky")
    return kinds


def is_corroborated(cal: dict) -> bool:
    """Jev disagrees with the human label and every other source on the frame agrees with Jev.

    A frame with no other source is never corroborated: one metadata opinion
    against a person who looked at the image is not enough to rank it first.
    """
    roof = roof_conflict(cal)
    if roof:
        others = _other_roof_opinions(cal)
        if others and all(o == roof[1] for o in others):
            return True
    sky = sky_conflict(cal)
    if sky:
        ai_sky = (cal.get("ai_suggestion") or {}).get("sky_condition")
        if ai_sky in SKY_LEVELS and ai_sky == sky[1]:
            return True
    return False


def review_priority(cal: dict) -> float:
    """Sort key for a review queue: corroborated first, then by Jev's confidence."""
    jev = jev_block(cal) or {}
    conf = 0.0
    if roof_conflict(cal):
        conf = max(conf, float(jev.get("roof_confidence", 0.0)))
    if sky_conflict(cal):
        conf = max(conf, float(jev.get("sky_confidence", 0.0)))
    return (2.0 if is_corroborated(cal) else 1.0) + conf if conf or conflict_kinds(cal) else 0.0


def describe_jev(cal: dict) -> str:
    """Text for the labeling tool's Jev panel."""
    jev = jev_block(cal)
    if not jev:
        return ("No Jev audit for this frame.\n\n"
                "Run  python ml/jev_label_audit.py  to audit the labelled set.")
    seen = ["image stats"]
    if jev.get("nina_in_state"):
        seen.append("NINA roof")
    if jev.get("weather_in_state"):
        seen.append("weather")
    lines = [f"━━━ Jev (metadata only: {', '.join(seen)}) ━━━"]
    if "roof_open" in jev:
        lines.append(f"Roof:   {'OPEN' if jev['roof_open'] else 'CLOSED'} "
                     f"(p open {jev.get('roof_p_open', 0):.0%}, conf {jev.get('roof_confidence', 0):.0%})")
    if jev.get("sky_condition"):
        lines.append(f"Sky:    {jev['sky_condition']} (conf {jev.get('sky_confidence', 0):.0%})")
        probs = jev.get("sky_probabilities") or {}
        if probs:
            lines.append("        " + "  ".join(f"{k} {v:.0%}" for k, v in probs.items()))
    if "stars_p" in jev:
        lines.append(f"Stars:  p {jev['stars_p']:.0%}")
    lines.append(f"Model:  {jev.get('model', '?')}")

    labels = cal.get("labels") or {}
    if labels.get("labeled_at"):
        lines.append("\nvs Manual label:")
        roof = roof_conflict(cal)
        if "roof_open" in jev:
            lines.append("  Roof: ✗ MISMATCH" if roof else "  Roof: ✓")
        sky = sky_conflict(cal)
        if sky:
            lines.append(f"  Sky:  ✗ (manual: {sky[0]})")
        elif jev.get("sky_condition") and to_bool(labels.get("roof_open")) \
                and labels.get("sky_condition") in SKY_LEVELS:
            lines.append("  Sky:  ✓")
        if is_corroborated(cal):
            lines.append("  ⚠ every other source sides with Jev — check this label")
    return "\n".join(lines)


def banner_text(cal: dict):
    """One-line warning for the labeling tab, or None when Jev and the label agree."""
    parts = []
    roof = roof_conflict(cal)
    if roof:
        parts.append(f"roof {'OPEN' if roof[1] else 'CLOSED'} vs manual {'OPEN' if roof[0] else 'CLOSED'}")
    sky = sky_conflict(cal)
    if sky:
        parts.append(f"sky {sky[1]} vs manual {sky[0]}")
    if not parts:
        return None
    tail = "  ·  NINA/AI side with Jev" if is_corroborated(cal) else "  ·  metadata only, judge by eye"
    return "⚠️ JEV DISAGREES — " + "; ".join(parts) + tail
