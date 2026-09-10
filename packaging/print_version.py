"""Print the version that build.bat should label the binary with.

Kept as a file rather than an inline ``python -c`` snippet: cmd's ``for /f``
delimits its command with single quotes, so a snippet containing
``sys.path.insert(0, 'src')`` would be cut off at the first quote — which is
exactly how the label ended up as "unknown" the first time.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import openminis  # noqa: E402

print(openminis.__version__)
