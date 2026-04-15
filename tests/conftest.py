import sys
from pathlib import Path

# Add src/ to sys.path so tests can import top-level modules (core, api, ...).
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
