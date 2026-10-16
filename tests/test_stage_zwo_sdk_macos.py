"""scripts/ci/stage_zwo_sdk_macos.py — the pure parts: reading otool output to
find the libusb reference and the rpaths. The lipo / install_name_tool /
codesign steps need a Mac and run in the macOS build job."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "ci"))
import stage_zwo_sdk_macos as stage  # noqa: E402

# The shape of `otool -L` on ZWO's V1.41 arm64 slice (version numbers illustrative).
OTOOL_L_ZWO = """libASICamera2.dylib:
\t@loader_path/libASICamera2.dylib (compatibility version 0.0.0, current version 0.0.0)
\t/opt/homebrew/opt/libusb/lib/libusb-1.0.0.dylib (compatibility version 6.0.0, current version 6.0.0)
\t/usr/lib/libc++.1.dylib (compatibility version 1.0.0, current version 1700.255.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1345.100.2)
"""

OTOOL_L_NO_USB = """libASICamera2.dylib:
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1345.100.2)
"""

OTOOL_l_RPATHS = """Load command 17
          cmd LC_RPATH
      cmdsize 32
         path @loader_path (offset 12)
Load command 18
          cmd LC_RPATH
      cmdsize 32
         path /opt/homebrew/lib (offset 12)
"""


def test_finds_zwos_absolute_homebrew_libusb():
    assert stage.libusb_dependency(OTOOL_L_ZWO) == "/opt/homebrew/opt/libusb/lib/libusb-1.0.0.dylib"


def test_finds_an_already_rpath_relative_libusb():
    patched = OTOOL_L_ZWO.replace("/opt/homebrew/opt/libusb/lib/", "@rpath/")
    assert stage.libusb_dependency(patched) == "@rpath/libusb-1.0.0.dylib"


def test_no_libusb_is_none():
    assert stage.libusb_dependency(OTOOL_L_NO_USB) is None


def test_the_librarys_own_id_line_is_not_a_dependency():
    own_id_only = "libusb-1.0.0.dylib:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
    assert stage.libusb_dependency(own_id_only) is None


def test_reads_every_rpath():
    assert stage.rpaths(OTOOL_l_RPATHS) == ["@loader_path", "/opt/homebrew/lib"]


def test_licence_and_source_are_in_the_repo():
    assert os.path.isfile(stage.ZWO_LICENCE)
    assert os.path.basename(stage.SOURCE_LIB) == stage.CAMERA_LIB


def test_refuses_to_run_off_macos(monkeypatch):
    monkeypatch.setattr(stage.sys, "platform", "win32")
    monkeypatch.setattr(stage.sys, "argv", ["stage_zwo_sdk_macos.py"])
    assert stage.main() == 1
