import os
import sys
from pathlib import Path

# Make src/ importable without an editable install.
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

# Tests never hit the network: force fake clients everywhere.
os.environ["PRODUCT_INTEL_FAKE"] = "1"
