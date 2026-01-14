"""
Update version in PFRSentinel_macOS.spec from version.py
Run this before building to sync versions
"""
import re
from pathlib import Path

# Determine project root (handle scripts/ directory)
script_dir = Path(__file__).parent
if script_dir.name == 'scripts':
    project_root = script_dir.parent
else:
    project_root = script_dir

# Read version from version.py
version_py = project_root / "version.py"
content = version_py.read_text()
match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)

if match:
    version = match.group(1)
    
    # Update PFRSentinel_macOS.spec
    spec_file = project_root / "PFRSentinel_macOS.spec"
    if spec_file.exists():
        spec_content = spec_file.read_text()
        
        # Update CFBundleShortVersionString
        spec_content = re.sub(
            r"'CFBundleShortVersionString': '[^']+'",
            f"'CFBundleShortVersionString': '{version}'",
            spec_content
        )
        
        # Update CFBundleVersion
        spec_content = re.sub(
            r"'CFBundleVersion': '[^']+'",
            f"'CFBundleVersion': '{version}'",
            spec_content
        )
        
        spec_file.write_text(spec_content)
        print(f"✓ Updated PFRSentinel_macOS.spec with version {version}")
    else:
        print(f"⚠ PFRSentinel_macOS.spec not found")
else:
    print("✗ Could not find version in version.py")
    exit(1)
