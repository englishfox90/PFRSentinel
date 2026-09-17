"""Execute PFRSentinel.spec on any OS without running a build.

A PyInstaller spec is plain Python, executed with a few globals injected and
the build classes in scope. Running it with those classes stubbed exercises
every line of module-scope logic — version parsing, the ``collect_all`` sweeps,
the data-file existence checks — while costing seconds instead of the minutes a
real freeze takes.

That is the gate the macOS and Linux builds need (issues #38 and #41): before
this, ``from PyInstaller.utils.win32.versioninfo import ...`` at module scope
meant the spec could not even be *parsed* off Windows, and nothing in CI would
have noticed a second Windows-only import creeping in.

Exits non-zero with the offending traceback when the spec cannot be executed,
or when the objects it builds fail the platform invariants below.
"""
from __future__ import annotations

import os
import sys
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC_PATH = os.path.join(REPO_ROOT, "PFRSentinel.spec")


class _Recorder:
    """Stands in for Analysis/PYZ/EXE/COLLECT and remembers its arguments."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        # Analysis exposes these as attributes; the spec reads them back when
        # it builds PYZ and COLLECT.
        self.pure = []
        self.zipped_data = []
        self.scripts = []
        self.binaries = []
        self.zipfiles = []
        self.datas = list(kwargs.get("datas", []))


def _run_spec() -> dict:
    """Execute the spec with stubbed build classes; return its namespace."""
    recorded: dict[str, list[_Recorder]] = {}

    def _make(name):
        def factory(*args, **kwargs):
            obj = _Recorder(*args, **kwargs)
            recorded.setdefault(name, []).append(obj)
            return obj
        return factory

    namespace = {
        "__file__": SPEC_PATH,
        "__name__": "__main__",
        # PyInstaller injects these; the spec uses SPEC to locate the repo root.
        "SPEC": SPEC_PATH,
        "DISTPATH": os.path.join(REPO_ROOT, "dist"),
        "workpath": os.path.join(REPO_ROOT, "build"),
        "warnfile": "",
        "Analysis": _make("Analysis"),
        "PYZ": _make("PYZ"),
        "EXE": _make("EXE"),
        "COLLECT": _make("COLLECT"),
        "BUNDLE": _make("BUNDLE"),
        "TOC": list,
        "Tree": _make("Tree"),
        "Splash": _make("Splash"),
        "MERGE": _make("MERGE"),
    }

    with open(SPEC_PATH, encoding="utf-8") as handle:
        source = handle.read()

    # The spec resolves its data files relative to the working directory.
    previous_cwd = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        exec(compile(source, SPEC_PATH, "exec"), namespace)
    finally:
        os.chdir(previous_cwd)

    namespace["_recorded"] = recorded
    return namespace


def _check_invariants(namespace: dict) -> list[str]:
    failures = []
    recorded = namespace["_recorded"]

    for required in ("Analysis", "PYZ", "EXE", "COLLECT"):
        if not recorded.get(required):
            failures.append(f"spec never called {required}()")
    if failures:
        return failures

    exe_kwargs = recorded["EXE"][0].kwargs
    is_windows = sys.platform == "win32"

    version = exe_kwargs.get("version")
    if is_windows and version is None:
        failures.append("EXE(version=...) is None on Windows — the VSVersionInfo "
                        "resource is what keeps AV heuristics calm")
    if not is_windows and version is not None:
        failures.append(f"EXE(version={version!r}) is set off Windows — "
                        "VSVersionInfo is a PE-only structure")

    icon = exe_kwargs.get("icon")
    if not is_windows and isinstance(icon, str) and icon.lower().endswith(".ico"):
        failures.append(f"EXE(icon={icon!r}) — .ico is Windows-only; macOS needs .icns")

    # A datas entry whose source is missing aborts a real build with a message
    # that does not name the entry. Catch it here instead.
    for source, _dest in recorded["Analysis"][0].kwargs.get("datas", []):
        if not os.path.exists(os.path.join(REPO_ROOT, source)):
            failures.append(f"datas entry missing on disk: {source}")

    return failures


def main() -> int:
    print(f"Parsing {os.path.relpath(SPEC_PATH, REPO_ROOT)} on {sys.platform} "
          f"(Python {sys.version.split()[0]})")
    try:
        namespace = _run_spec()
    except Exception:
        print("FAIL: the spec raised while being executed.\n", file=sys.stderr)
        traceback.print_exc()
        return 1

    failures = _check_invariants(namespace)
    if failures:
        print("\nFAIL: spec executed but failed platform invariants:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    analysis = namespace["_recorded"]["Analysis"][0]
    print(f"OK: spec parses. {len(analysis.kwargs.get('datas', []))} data files, "
          f"{len(analysis.kwargs.get('hiddenimports', []))} hidden imports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
