"""scripts/ci/set_dev_version.py — the version a CI dev build carries."""
import importlib.util
import re
from functools import lru_cache
from pathlib import Path

import pytest

from services.update_checker import compare_versions

REPO_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def load_stamper():
    path = REPO_ROOT / "scripts" / "ci" / "set_dev_version.py"
    spec = importlib.util.spec_from_file_location("set_dev_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_newest_release_tag_compares_numerically_and_skips_non_releases():
    tags = ["3.6.6", "v3.2.9", "v3.2.12", "v3.7.6", "v3.7.0", "dev-latest", "v3.8.0-dev.2"]
    assert load_stamper().newest_release_tag(tags) == "v3.7.6"
    assert load_stamper().newest_release_tag(["dev-latest", "3.6.6"]) is None


def test_committed_version_equal_to_the_release_bumps_the_patch():
    assert load_stamper().dev_version("3.7.6", "v3.7.6", 14) == "3.7.7-dev.14"


def test_committed_version_already_ahead_of_the_release_is_kept():
    assert load_stamper().dev_version("3.8.0", "v3.7.6", 3) == "3.8.0-dev.3"


def test_committed_version_behind_the_newest_tag_follows_the_tag():
    assert load_stamper().dev_version("3.7.5", "v3.7.6", 0) == "3.7.7-dev.0"


@pytest.mark.parametrize("committed", ["3.7.7-dev.1", "3.7"])
def test_an_already_stamped_or_malformed_version_is_refused(committed):
    with pytest.raises(ValueError):
        load_stamper().dev_version(committed, "v3.7.6", 1)


def test_counter_must_fit_a_windows_version_field():
    stamper = load_stamper()
    assert stamper.dev_version("3.7.6", "v3.7.6", stamper.MAX_COUNTER)
    with pytest.raises(ValueError):
        stamper.dev_version("3.7.6", "v3.7.6", stamper.MAX_COUNTER + 1)


def test_file_version_puts_the_counter_in_the_fourth_slot():
    stamper = load_stamper()
    assert stamper.file_version("3.7.7-dev.14") == "3.7.7.14"
    assert stamper.file_version("3.7.6") == "3.7.6.0"


def test_spec_parses_versions_the_same_way():
    """PFRSentinel.spec cannot import the script, so it repeats the pattern."""
    spec_text = (REPO_ROOT / "PFRSentinel.spec").read_text(encoding="utf-8")
    pattern = re.search(r"re\.match\(r'([^']+)', version_str\)", spec_text).group(1)
    for version in ("3.7.6", "3.7.7-dev.14"):
        groups = re.match(pattern, version).groups()
        assert ".".join(g or "0" for g in groups) == load_stamper().file_version(version)


def test_stamp_rewrites_version_py_in_place(tmp_path):
    target = tmp_path / "version.py"
    target.write_text('"""Application version"""\n__version__ = "3.7.6"\n', encoding="utf-8")
    assert load_stamper().stamp(target, "3.7.7-dev.14") == "3.7.6"
    assert target.read_text(encoding="utf-8") == '"""Application version"""\n__version__ = "3.7.7-dev.14"\n'


def test_dev_build_sorts_between_the_releases_around_it():
    dev = load_stamper().dev_version("3.7.6", "v3.7.6", 14)
    assert compare_versions("3.7.6", dev) == -1
    assert compare_versions(dev, "3.7.7") == -1
