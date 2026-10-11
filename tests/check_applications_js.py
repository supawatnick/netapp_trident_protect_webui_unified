"""Check inline JavaScript in the Jinja Applications template with Node.

No template rendering needed: the script is plain JavaScript, not Jinja
interpolated code, so syntax can be checked without a running cluster.
"""
from pathlib import Path
import re
import subprocess
import sys

path = Path("app/templates/applications.html")
source = path.read_text(encoding="utf-8")
scripts = re.findall(r"<script(?:\\s[^>]*)?>(.*?)</script>", source, flags=re.DOTALL | re.IGNORECASE)
if not scripts:
    raise SystemExit(f"No inline script found in {path}")

for idx, script in enumerate(scripts, start=1):
    result = subprocess.run(["node", "--check"], input=script, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        sys.stderr.write(f"{path}: inline script #{idx} failed JS syntax validation\n")
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
print(f"Validated {len(scripts)} inline script(s) in {path}")
