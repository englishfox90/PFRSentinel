"""zwo_sdk_library — per-OS SDK library name, search order and config repair."""
import os

import pytest

from services import host_platform
from services import zwo_sdk_library as lib

WINDOWS_PATH = r"C:\Program Files (x86)\PFRSentinel\_internal\ASICamera2.dll"


@pytest.fixture
def as_platform(monkeypatch):
    """Pretend to be another OS for both the name and the search rules."""
    def _set(name):
        flags = {
            "windows": (True, False),
            "macos": (False, True),
            "linux": (False, False),
        }[name]
        for module in (host_platform, lib):
            monkeypatch.setattr(module, "IS_WINDOWS", flags[0])
            monkeypatch.setattr(module, "IS_MACOS", flags[1])
        monkeypatch.setattr(host_platform, "IS_LINUX", name == "linux")
    return _set


@pytest.fixture
def isolated_search(monkeypatch, tmp_path):
    """Search only tmp_path, and keep the loader's own search out of it."""
    def _set(*names):
        paths = [str(tmp_path / n) for n in names]
        monkeypatch.setattr(lib, "candidate_sdk_paths", lambda: paths)
        monkeypatch.setattr(lib, "find_library", lambda stem: None)
        return paths
    return _set


@pytest.mark.parametrize("name, expected", [
    ("windows", "ASICamera2.dll"),
    ("macos", "libASICamera2.dylib"),
    ("linux", "libASICamera2.so"),
])
def test_candidates_use_the_platform_library_name(as_platform, name, expected):
    as_platform(name)
    paths = lib.candidate_sdk_paths()
    assert paths
    assert all(os.path.basename(p) == expected for p in paths)
    assert len(paths) == len(set(paths))


def test_project_root_is_searched_first_when_running_from_source(as_platform):
    as_platform("macos")
    root = os.path.dirname(os.path.dirname(os.path.abspath(lib.__file__)))
    first = lib.candidate_sdk_paths()[0]
    assert os.path.normpath(os.path.dirname(first)) == os.path.normpath(root)


def test_macos_searches_usr_local_and_homebrew(as_platform):
    as_platform("macos")
    dirs = {os.path.dirname(p).replace("\\", "/") for p in lib.candidate_sdk_paths()}
    assert "/usr/local/lib" in dirs
    assert "/opt/homebrew/lib" in dirs


def test_linux_searches_the_multiarch_dir(as_platform, monkeypatch):
    as_platform("linux")
    monkeypatch.setattr(lib.platform, "machine", lambda: "aarch64")
    dirs = {os.path.dirname(p).replace("\\", "/") for p in lib.candidate_sdk_paths()}
    assert "/usr/lib/aarch64-linux-gnu" in dirs


@pytest.mark.parametrize("name, path, native", [
    ("macos", WINDOWS_PATH, False),
    ("macos", "/usr/local/lib/libASICamera2.dylib", True),
    ("linux", "/usr/lib/libASICamera2.so.1.36", True),
    ("linux", "/usr/local/lib/libASICamera2.dylib", False),
    ("windows", WINDOWS_PATH, True),
    ("windows", "", False),
])
def test_is_native_sdk_path(as_platform, name, path, native):
    as_platform(name)
    assert lib.is_native_sdk_path(path) is native


def test_find_returns_the_first_existing_candidate(as_platform, isolated_search, tmp_path):
    as_platform("macos")
    missing, present, also_present = isolated_search("a.dylib", "b.dylib", "c.dylib")
    open(present, "w").close()
    open(also_present, "w").close()
    assert lib.find_sdk_library() == present


def test_find_falls_back_to_the_loader_search(as_platform, isolated_search, monkeypatch):
    as_platform("linux")
    isolated_search("nothing.so")
    monkeypatch.setattr(lib, "find_library", lambda stem: "libASICamera2.so" if stem == "ASICamera2" else None)
    assert lib.find_sdk_library() == "libASICamera2.so"


def test_find_returns_none_when_nothing_is_installed(as_platform, isolated_search):
    as_platform("macos")
    isolated_search("nothing.dylib")
    assert lib.find_sdk_library() is None


def test_resolve_keeps_a_usable_configured_path(as_platform, isolated_search, tmp_path):
    as_platform("macos")
    (found,) = isolated_search("found.dylib")
    open(found, "w").close()
    configured = tmp_path / "mine" / "libASICamera2.dylib"
    configured.parent.mkdir()
    configured.write_bytes(b"")
    assert lib.resolve_sdk_path(str(configured)) == str(configured)


def test_resolve_replaces_a_windows_path_on_macos(as_platform, isolated_search):
    as_platform("macos")
    (found,) = isolated_search("libASICamera2.dylib")
    open(found, "w").close()
    assert lib.resolve_sdk_path(WINDOWS_PATH) == found


def test_resolve_ignores_a_foreign_file_even_if_it_exists(as_platform, isolated_search, tmp_path):
    as_platform("macos")
    isolated_search("nothing.dylib")
    dll = tmp_path / "ASICamera2.dll"
    dll.write_bytes(b"")
    assert lib.resolve_sdk_path(str(dll)) is None


def test_repair_rewrites_a_config_moved_from_windows(as_platform, isolated_search):
    as_platform("macos")
    (found,) = isolated_search("libASICamera2.dylib")
    open(found, "w").close()
    data = {"zwo_sdk_path": WINDOWS_PATH}
    notice = lib.repair_configured_sdk_path(data)
    assert data["zwo_sdk_path"] == found
    assert WINDOWS_PATH in notice and found in notice


def test_repair_fills_an_empty_path(as_platform, isolated_search):
    as_platform("linux")
    (found,) = isolated_search("libASICamera2.so")
    open(found, "w").close()
    data = {}
    assert lib.repair_configured_sdk_path(data)
    assert data["zwo_sdk_path"] == found


def test_repair_leaves_a_working_path_alone(as_platform, isolated_search, tmp_path):
    as_platform("macos")
    (found,) = isolated_search("other.dylib")
    open(found, "w").close()
    mine = tmp_path / "libASICamera2.dylib"
    mine.write_bytes(b"")
    data = {"zwo_sdk_path": str(mine)}
    assert lib.repair_configured_sdk_path(data) is None
    assert data["zwo_sdk_path"] == str(mine)


def test_repair_keeps_the_old_value_when_nothing_is_found(as_platform, isolated_search):
    as_platform("macos")
    isolated_search("nothing.dylib")
    data = {"zwo_sdk_path": WINDOWS_PATH}
    assert lib.repair_configured_sdk_path(data) is None
    assert data["zwo_sdk_path"] == WINDOWS_PATH


def test_config_load_repairs_a_foreign_sdk_path(as_platform, isolated_search, tmp_path):
    import json
    from services.config import Config

    as_platform("macos")
    (found,) = isolated_search("libASICamera2.dylib")
    open(found, "w").close()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"zwo_sdk_path": WINDOWS_PATH}))

    config = Config(config_path=str(config_path))
    assert config.get("zwo_sdk_path") == found
