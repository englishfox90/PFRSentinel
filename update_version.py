"""
Generate version.iss from version.py for Inno Setup
Run this before building installer to sync versions
"""
import re
from pathlib import Path

# Read version from version.py
version_py = Path(__file__).parent / "version.py"
content = version_py.read_text()
match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)

if match:
    version = match.group(1)
    
    # Write version.iss
    version_iss = Path(__file__).parent / "version.iss"
    version_iss.write_text(f'; Auto-generated from version.py - DO NOT EDIT MANUALLY\n#define MyAppVersion "{version}"\n')
    
    print(f"✓ Generated version.iss with version {version}")
else:
    print("✗ Could not find version in version.py")
    exit(1)
