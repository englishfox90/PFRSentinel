#!/usr/bin/env python
"""PostToolUse hook — warn when business-logic markers appear in ui/panels/.

Panels should be UI only. This is a warning, not a block — some legitimate uses
exist (e.g. `np` for display scaling in live_monitoring.py). Exit 0 always.
"""
import json
import os
import re
import sys

# Detect imports/uses commonly associated with business logic.
# Keep this list conservative — false positives erode trust in the warning.
PATTERNS = [
    (r"\bimport\s+requests\b|\bfrom\s+requests\b", "requests (HTTP) — move to a controller or service"),
    (r"\bimport\s+threading\b|\bfrom\s+threading\b", "threading — controllers own background work"),
    (r"\bimport\s+subprocess\b|\bfrom\s+subprocess\b", "subprocess — wrap in a service"),
    (r"\bimport\s+asyncio\b", "asyncio — move to a controller"),
    (r"\bThreadPoolExecutor\b", "ThreadPoolExecutor — move to a controller"),
    (r"\bcv2\.(cvtColor|imread|imwrite|VideoCapture|imdecode|imencode)\b", "cv2 image processing — belongs in services/processor.py"),
    (r"\bwebsocket\.|websockets\.", "websocket — move to a service"),
]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    path = (data.get("tool_input") or {}).get("file_path", "")
    if not path or not path.endswith(".py"):
        return 0

    rel = os.path.relpath(path).replace("\\", "/")
    if not rel.startswith("ui/panels/"):
        return 0

    if not os.path.exists(path):
        return 0

    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return 0

    hits = []
    for pattern, description in PATTERNS:
        if re.search(pattern, text):
            hits.append(description)

    if hits:
        print(
            f"WARN: {rel} contains business-logic markers (panels should be UI only):",
            file=sys.stderr,
        )
        for h in hits:
            print(f"  - {h}", file=sys.stderr)
        print(
            "See .claude/rules/ui-panels.md. Move logic to ui/controllers/ "
            "if this is new code.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
