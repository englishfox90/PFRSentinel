"""Per-file size policy: the caps, the frozen exceptions, and how lines are counted.

Single source of truth for two consumers:
- .claude/hooks/check_file_size.py (local Claude Code hook; tracked, so CI reads it too)
- scripts/ci/check_file_sizes.py (CI audit of every tracked source file)
"""

HARD_CAP = 750
WARN_CAP = 600

# Frozen ceilings for files that were already over the cap when the cap was
# introduced (+5% above that day's line count). Do not raise these without a
# commit that actually splits the file.
EXCEPTIONS = {
    "ui/panels/timelapse_panel.py": 750,
    "ml/train_sky_classifier.py": 743,
    "ui/panels/live_monitoring.py": 723,
    "tests/test_camera.py": 1430,
    # Frozen 2026-09-15 when CI started auditing every tracked file.
    "services/camera/camera_connection.py": 788,
    "scripts/dev/image/analyze_raw.py": 1284,
}


def count_lines(text: str) -> int:
    """Match `wc -l` semantics; a trailing newline-less line still counts."""
    if not text:
        return 0
    n = text.count("\n")
    if not text.endswith("\n"):
        n += 1
    return n
