"""Import the checkout as a plugin package without installing its source."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["ASTRBOT_ROOT"] = str(Path(tempfile.gettempdir()) / "persona-sdk-tests")
