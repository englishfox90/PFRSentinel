#!/usr/bin/env python3
"""Audit human labels against TypeSafe Jev, a text-only decision model.

Jev cannot see the frame. It is given only the numbers already stored in each
calibration JSON (image statistics, exposure, time, moon, weather, NINA roof
state), rewritten as named buckets because the model reads numbers as text.
It answers typed questions with calibrated probabilities. So this is a test
of whether the *metadata* predicts the human label, and every confident
disagreement is a frame worth a second look: either the label is wrong or the
metadata is (a stale weather feed, a NINA reading from a different moment).

Each verdict is stored under `jev_audit` in the frame's calibration JSON (the
labels block is never touched — same locked read-modify-write the AI
pre-labeller uses), so the labeling tool can show it beside the frame and the
Review tab can filter on it. Responses are cached and the report lands in
`<data_dir>/_jev_audit/`, an underscore folder the dataset walker skips.

Usage (needs OPENROUTER_API_KEY; ~$0.10 for the whole labelled set):
    python ml/jev_label_audit.py --dry-run          # show sample states, no API calls
    python ml/jev_label_audit.py --sample 200       # random subset first
    python ml/jev_label_audit.py                    # every human-labelled frame
    python ml/jev_label_audit.py --no-nina          # hide the safety monitor: image stats only
    python ml/jev_label_audit.py --include-unlabeled  # audit new frames too
    python ml/jev_label_audit.py --no-store         # report only, leave the JSONs alone
"""
import argparse
import csv
import hashlib
import json
import os
import random
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.calibration_store import load_calibration, update_calibration  # noqa: E402
from ml.dataset_files import iter_calibration_files  # noqa: E402
from ml.jev_review import JEV_KEY, make_jev_block  # noqa: E402
from ml.label_suggestion import SKY_LEVELS, to_bool  # noqa: E402

OPENROUTER_URL = "https://openrouter.ai/api/v1/systemone"
DEFAULT_MODEL = "typesafe/jev-1.13"
TRANSIENT_STATUS = {429, 500, 502, 503, 504, 529}


# --- state: numbers become words -------------------------------------------

def _bucket(value, edges, names):
    """Name the interval `value` falls in. edges ascending, len(names) == len(edges) + 1."""
    if value is None:
        return "unknown"
    for edge, name in zip(edges, names):
        if value < edge:
            return name
    return names[-1]


def _exposure_seconds(text):
    try:
        return float(str(text).rstrip("s"))
    except (TypeError, ValueError):
        return None


def build_state(cal: dict, use_nina: bool, use_weather: bool) -> dict:
    """Everything the model may see. Labels, AI suggestions and CNN output are never included."""
    stretch = cal.get("stretch", {})
    pct = cal.get("percentiles", {})
    corner = cal.get("corner_analysis", {})
    tc = cal.get("time_context", {})
    mc = cal.get("moon_context", {})

    exp_s = _exposure_seconds(cal.get("exposure"))
    median = stretch.get("median_lum")
    p50, p99, p1 = pct.get("p50"), pct.get("p99"), pct.get("p1")
    highlight_spread = (p99 - p50) if (p99 is not None and p50 is not None) else None
    shadow_spread = (p50 - p1) if (p50 is not None and p1 is not None) else None

    state = {
        "camera": {
            "exposure": _bucket(exp_s, [1, 5, 15, 25],
                                ["very short (under 1 s)", "short (1 to 5 s)",
                                 "medium (5 to 15 s)", "long (15 to 25 s)",
                                 "maximum (25 s or more)"]),
            "gain": _bucket(_exposure_seconds(cal.get("gain")), [100, 250, 400],
                            ["low", "medium", "high", "very high"]),
        },
        "time": {
            "period": tc.get("detailed_period") or tc.get("period") or "unknown",
            "astronomical_night": "yes" if to_bool(tc.get("is_astronomical_night")) else "no",
        },
        "moon": {
            "above_horizon": "yes" if to_bool(mc.get("moon_is_up")) else "no",
            "phase": (mc.get("phase_name") or "unknown").replace("_", " "),
            "illumination": _bucket(mc.get("illumination_pct"), [10, 40, 70, 95],
                                    ["new", "crescent", "half", "gibbous", "full"]),
        },
        "image_statistics": {
            "overall_brightness": _bucket(median, [0.04, 0.12, 0.30, 0.55],
                                          ["very dark", "dark", "mid grey", "bright", "very bright"]),
            "dark_scene_flag": "yes" if to_bool(stretch.get("is_dark_scene")) else "no",
            "contrast": _bucket(stretch.get("dynamic_range"), [0.05, 0.15, 0.35],
                                ["almost flat (uniform frame)", "low", "moderate", "high"]),
            "bright_highlights_above_median": _bucket(
                highlight_spread, [0.02, 0.06, 0.15],
                ["none (flat)", "faint", "clear", "strong"]),
            "shadow_depth_below_median": _bucket(
                shadow_spread, [0.02, 0.06, 0.15],
                ["none (flat)", "shallow", "moderate", "deep"]),
            "corners_versus_centre": _bucket(
                corner.get("corner_to_center_ratio"), [0.6, 0.9, 1.1],
                ["corners much darker than centre", "corners somewhat darker",
                 "corners and centre similar", "corners brighter than centre"]),
            "corner_unevenness": _bucket(corner.get("corner_stddev"), [0.02, 0.06, 0.12],
                                         ["even", "slightly uneven", "uneven", "very uneven"]),
        },
    }

    if use_weather:
        wx = cal.get("weather_context", {})
        if to_bool(wx.get("available")):
            state["weather_service"] = {
                "reported_condition": wx.get("description") or wx.get("condition") or "unknown",
                "cloud_cover": _bucket(wx.get("cloud_coverage_pct"), [11, 36, 66, 90],
                                       ["clear (0 to 10 percent)", "few clouds (11 to 35 percent)",
                                        "scattered (36 to 65 percent)", "broken (66 to 90 percent)",
                                        "overcast (over 90 percent)"]),
                "humidity": _bucket(wx.get("humidity_pct"), [50, 75, 90],
                                    ["dry", "moderate", "humid", "saturated"]),
                "note": "regional forecast feed, may be stale or wrong for this site",
            }
        else:
            state["weather_service"] = "not available"

    if use_nina:
        rs = cal.get("roof_state", {})
        if to_bool(rs.get("available")) and rs.get("roof_open") is not None:
            state["observatory_safety_monitor"] = {
                "roof": "open" if to_bool(rs.get("roof_open")) else "closed",
                "note": "reported by the observatory control software at capture time",
            }
        else:
            state["observatory_safety_monitor"] = "not available"

    return state


QUESTIONS = {
    "roof": {
        "type": "choice",
        "instructions": (
            "A camera on a telescope pier looks up through a roll-off metal roof. "
            "From the capture metadata, was the roof open (camera sees the sky) or "
            "closed (camera sees the underside of the roof) when this frame was taken? "
            "A closed roof at night gives a very dark, almost flat frame with no "
            "highlights; an open roof gives a frame with contrast, bright points and a "
            "sky gradient."),
        "criteria": {
            "open": "The roof was open and the camera was looking at the sky.",
            "closed": "The roof was closed and the sky was blocked.",
        },
    },
    "sky": {
        "type": "choice",
        "instructions": (
            "Assume the roof was open. How cloudy was the sky in this frame? Judge from "
            "the image statistics first and treat the weather service as a weak hint."),
        "criteria": {
            "Clear": "Mostly clear sky: sharp stars, dark background, at most a little haze.",
            "Partly Cloudy": "Noticeable cloud patches covering part of the field.",
            "Overcast": "Most or all of the sky hidden: washed-out, uniform murk or fog.",
        },
    },
    "stars": {
        "type": "noul",
        "instructions": "Assume the roof was open. Were individual stars visible in this frame?",
    },
}


# --- API ---------------------------------------------------------------------

def ask_jev(state: dict, model: str, timeout: int = 60, max_retries: int = 4) -> dict:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY environment variable is not set")
    payload = {"model": model, "state": state, "questions": QUESTIONS}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code in TRANSIENT_STATUS and attempt < max_retries:
            time.sleep(delay)
            delay *= 2
            continue
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()
    raise RuntimeError("unreachable")


def _state_key(state: dict, model: str) -> str:
    blob = json.dumps({"state": state, "questions": QUESTIONS, "model": model}, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def store_jev_audit(cal_path, block: dict):
    """Write the verdict into the file as it is now; a label saved meanwhile survives."""
    def add_block(cal):
        cal[JEV_KEY] = block

    update_calibration(cal_path, add_block)


class ResponseCache:
    """Append-only JSONL so a re-run, or a changed flag, never pays twice for one state.

    Workers write concurrently, and appends from separate handles are not atomic
    on Windows, so one lock covers the dict and the file. A line that still
    fails to parse is skipped and counted, never fatal: that frame is simply
    fetched again.
    """

    def __init__(self, path: Path):
        self.path = path
        self.entries = {}
        self.skipped = 0
        self._lock = threading.Lock()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                        self.entries[row["key"]] = row["response"]
                    except (ValueError, KeyError, TypeError):
                        self.skipped += 1

    def get(self, key):
        with self._lock:
            return self.entries.get(key)

    def put(self, key, response):
        line = json.dumps({"key": key, "response": response}) + "\n"
        with self._lock:
            self.entries[key] = response
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)


# --- scoring -------------------------------------------------------------------

def _opt_bool(value):
    """Calibration booleans may be real bools or 'True'/'False' strings; absent stays None."""
    return None if value is None else to_bool(value)


def _answer_row(cal: dict, answers: dict) -> dict:
    labels = cal.get("labels", {})
    rs = cal.get("roof_state", {})
    ai = cal.get("ai_suggestion") or {}
    wx = cal.get("weather_context", {})
    roof = answers.get("roof", {})
    sky = answers.get("sky", {})
    stars = answers.get("stars", {})
    roof_probs = roof.get("probabilities", {})
    return {
        "file": "",
        "human_roof": _opt_bool(labels.get("roof_open")),
        "jev_roof": roof.get("choice") == "open" if roof.get("choice") else None,
        "jev_roof_p_open": round(roof_probs.get("open", float("nan")), 3),
        "jev_roof_conf": round(roof.get("confidence", 0.0), 3),
        "nina_roof": _opt_bool(rs.get("roof_open")) if to_bool(rs.get("available")) else None,
        "ai_roof": _opt_bool(ai.get("roof_open")),
        "human_sky": labels.get("sky_condition") or None,
        "jev_sky": sky.get("choice"),
        "jev_sky_conf": round(sky.get("confidence", 0.0), 3),
        "ai_sky": ai.get("sky_condition") or None,
        "human_stars": _opt_bool(labels.get("stars_visible")),
        "jev_stars_p": round(stars.get("noul", float("nan")), 3),
        "weather_cloud_pct": wx.get("cloud_coverage_pct") if to_bool(wx.get("available")) else None,
        "exposure": cal.get("exposure"),
        "period": cal.get("time_context", {}).get("detailed_period"),
        "label_source": labels.get("label_source") or "unknown",
    }


def _conf_band(c):
    return "high (>=0.9)" if c >= 0.9 else "mid (0.7-0.9)" if c >= 0.7 else "low (<0.7)"


def write_report(rows: list, out_dir: Path, model: str, use_nina: bool) -> str:
    lines = [f"Jev label audit  model={model}  frames={len(rows)}  nina_in_state={use_nina}", ""]

    roof_rows = [r for r in rows if r["human_roof"] is not None and r["jev_roof"] is not None]
    if roof_rows:
        hit = sum(r["human_roof"] == r["jev_roof"] for r in roof_rows)
        lines.append(f"ROOF  {hit}/{len(roof_rows)} agree with human  ({100 * hit / len(roof_rows):.1f}%)")
        conf = Counter(); conf_hit = Counter()
        cm = Counter()
        for r in roof_rows:
            b = _conf_band(r["jev_roof_conf"])
            conf[b] += 1; conf_hit[b] += r["human_roof"] == r["jev_roof"]
            cm[("open" if r["human_roof"] else "closed", "open" if r["jev_roof"] else "closed")] += 1
        for b in ("high (>=0.9)", "mid (0.7-0.9)", "low (<0.7)"):
            if conf[b]:
                lines.append(f"      confidence {b:14s} n={conf[b]:5d}  accuracy {100 * conf_hit[b] / conf[b]:.1f}%")
        lines.append("      confusion (human -> jev): " + ", ".join(
            f"{h}->{j}: {n}" for (h, j), n in sorted(cm.items())))
        nina_rows = [r for r in roof_rows if r["nina_roof"] is not None]
        if nina_rows:
            n_hit = sum(r["human_roof"] == r["nina_roof"] for r in nina_rows)
            lines.append(f"      for reference, NINA agrees with human on {n_hit}/{len(nina_rows)} "
                         f"({100 * n_hit / len(nina_rows):.1f}%) of the frames where it reported")
        ai_rows = [r for r in roof_rows if r["ai_roof"] is not None]
        if ai_rows:
            a_hit = sum(r["human_roof"] == r["ai_roof"] for r in ai_rows)
            lines.append(f"      for reference, the vision AI agrees with human on {a_hit}/{len(ai_rows)} "
                         f"({100 * a_hit / len(ai_rows):.1f}%)")
        lines.append("")

    sky_rows = [r for r in rows if r["human_sky"] in SKY_LEVELS and r["jev_sky"]]
    if sky_rows:
        hit = sum(r["human_sky"] == r["jev_sky"] for r in sky_rows)
        lines.append(f"SKY   {hit}/{len(sky_rows)} agree with human  ({100 * hit / len(sky_rows):.1f}%)"
                     "  (roof-open frames with a human sky label)")
        conf = Counter(); conf_hit = Counter()
        per_class = defaultdict(Counter)
        for r in sky_rows:
            b = _conf_band(r["jev_sky_conf"])
            conf[b] += 1; conf_hit[b] += r["human_sky"] == r["jev_sky"]
            per_class[r["human_sky"]][r["jev_sky"]] += 1
        for b in ("high (>=0.9)", "mid (0.7-0.9)", "low (<0.7)"):
            if conf[b]:
                lines.append(f"      confidence {b:14s} n={conf[b]:5d}  accuracy {100 * conf_hit[b] / conf[b]:.1f}%")
        for h in SKY_LEVELS:
            if per_class[h]:
                total = sum(per_class[h].values())
                lines.append(f"      human {h:13s} n={total:5d} -> jev " + ", ".join(
                    f"{j}: {n}" for j, n in per_class[h].most_common()))
        ai_rows = [r for r in sky_rows if r["ai_sky"] in SKY_LEVELS]
        if ai_rows:
            a_hit = sum(r["human_sky"] == r["ai_sky"] for r in ai_rows)
            lines.append(f"      for reference, the vision AI agrees with human on {a_hit}/{len(ai_rows)} "
                         f"({100 * a_hit / len(ai_rows):.1f}%)")
        lines.append("")

    star_rows = [r for r in rows if r["human_roof"] and r["human_stars"] is not None
                 and r["jev_stars_p"] == r["jev_stars_p"]]
    if star_rows:
        hit = sum((r["jev_stars_p"] >= 0.5) == bool(r["human_stars"]) for r in star_rows)
        lines.append(f"STARS {hit}/{len(star_rows)} agree with human at p>=0.5 "
                     f"({100 * hit / len(star_rows):.1f}%)  (roof-open frames)")
        lines.append("")

    text = "\n".join(lines)
    (out_dir / "report.txt").write_text(text, encoding="utf-8")
    return text


def write_csvs(rows: list, out_dir: Path):
    if not rows:
        return 0
    fields = list(rows[0].keys())
    with open(out_dir / "predictions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    def conflict_kind(r):
        kinds = []
        if r["human_roof"] is not None and r["jev_roof"] is not None and r["human_roof"] != r["jev_roof"]:
            others = [x for x in (r["nina_roof"], r["ai_roof"]) if x is not None]
            if others and all(o == r["jev_roof"] for o in others):
                kinds.append("ROOF: jev+nina/ai all disagree with human")
            else:
                kinds.append("ROOF: jev disagrees with human")
        if r["human_sky"] in SKY_LEVELS and r["jev_sky"] and r["human_sky"] != r["jev_sky"]:
            if r["ai_sky"] in SKY_LEVELS and r["ai_sky"] == r["jev_sky"]:
                kinds.append("SKY: jev+ai agree against human")
            else:
                kinds.append("SKY: jev disagrees with human")
        return "; ".join(kinds)

    conflicts = []
    for r in rows:
        kind = conflict_kind(r)
        if kind:
            conflicts.append({"conflict": kind, "priority": max(r["jev_roof_conf"], r["jev_sky_conf"]), **r})
    conflicts.sort(key=lambda r: (-("all disagree" in r["conflict"] or "agree against" in r["conflict"]),
                                  -r["priority"]))
    with open(out_dir / "conflicts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["conflict", "priority"] + fields)
        w.writeheader()
        w.writerows(conflicts)
    return len(conflicts)


# --- main ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Audit human labels against TypeSafe Jev (metadata only)")
    ap.add_argument("data_dir", nargs="?", default=r"D:\Pier Camera ML Data")
    ap.add_argument("--model", default=os.getenv("JEV_MODEL", DEFAULT_MODEL))
    ap.add_argument("--sample", type=int, default=0, help="Random N-frame subset (0 = all labelled)")
    ap.add_argument("--limit", type=int, default=0, help="First N frames only")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-nina", action="store_true", help="Hide the safety-monitor roof state from the model")
    ap.add_argument("--no-weather", action="store_true", help="Hide the weather feed from the model")
    ap.add_argument("--include-unlabeled", action="store_true",
                    help="Audit unlabelled frames as well (they count in no score, but the "
                         "labeling tool shows the verdict)")
    ap.add_argument("--no-store", action="store_true",
                    help="Do not write the verdict into the calibration JSONs")
    ap.add_argument("--dry-run", action="store_true", help="Print three example states and exit")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"ERROR: directory not found: {data_dir}")
        sys.exit(1)
    use_nina, use_weather = not args.no_nina, not args.no_weather

    frames = []
    for cal_path in iter_calibration_files(data_dir):
        cal = load_calibration(cal_path)
        if args.include_unlabeled or cal.get("labels", {}).get("labeled_at"):
            frames.append((cal_path, cal))
    print(f"{len(frames)} {'' if args.include_unlabeled else 'human-labelled '}frames in {data_dir}")
    if args.sample and args.sample < len(frames):
        random.Random(args.seed).shuffle(frames)
        frames = sorted(frames[:args.sample])
    if args.limit:
        frames = frames[:args.limit]

    if args.dry_run:
        for cal_path, cal in frames[:3]:
            print(f"\n--- {cal_path.name}  labels={cal.get('labels')}")
            print(json.dumps(build_state(cal, use_nina, use_weather), indent=2))
        est = sum(len(json.dumps(build_state(c, use_nina, use_weather))) for _, c in frames) / 4
        print(f"\n~{est / 1e6:.2f}M state tokens for {len(frames)} frames; "
              f"at $0.042/M that is under ${max(0.01, est / 1e6 * 0.042 * 3):.2f} with questions included")
        return

    out_dir = data_dir / "_jev_audit"
    out_dir.mkdir(exist_ok=True)
    tag = args.model.split("/")[-1] + ("" if use_nina else "_nonina") + ("" if use_weather else "_nowx")
    cache = ResponseCache(out_dir / f"responses_{tag}.jsonl")
    if cache.skipped:
        print(f"  cache: skipped {cache.skipped} unreadable line(s); those frames are fetched again")

    def work(item):
        cal_path, cal = item
        state = build_state(cal, use_nina, use_weather)
        key = _state_key(state, args.model)
        resp = cache.get(key)
        if resp is None:
            resp = ask_jev(state, args.model)
            cache.put(key, resp)
        answers = resp.get("answers", {})
        if not args.no_store:
            store_jev_audit(cal_path, make_jev_block(
                answers, resp.get("model", args.model), datetime.now().isoformat(),
                nina_in_state=use_nina, weather_in_state=use_weather))
        row = _answer_row(cal, answers)
        row["file"] = cal_path.name
        return row, resp.get("usage", {}).get("cost")

    rows, failures, cost = [], 0, 0.0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(work, item) for item in frames]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                row, c = fut.result()
                rows.append(row)
                cost += c or 0.0
            except Exception as exc:
                failures += 1
                if failures <= 5:
                    print(f"  failed: {exc}")
            if i % 200 == 0 or i == len(futures):
                print(f"  {i}/{len(futures)}  ok={len(rows)} failed={failures}  {time.time() - t0:.0f}s")

    rows.sort(key=lambda r: r["file"])
    n_conf = write_csvs(rows, out_dir)
    print()
    print(write_report(rows, out_dir, args.model, use_nina))
    print(f"{n_conf} conflicts -> {out_dir / 'conflicts.csv'}")
    print(f"all predictions -> {out_dir / 'predictions.csv'}   report -> {out_dir / 'report.txt'}")
    if not args.no_store:
        print(f"verdicts stored under '{JEV_KEY}' in {len(rows)} calibration JSONs "
              "(labeling tool: Review tab -> Jev filters)")
    if cost:
        print(f"spent this run (as reported by OpenRouter): ${cost:.4f}")


if __name__ == "__main__":
    main()
