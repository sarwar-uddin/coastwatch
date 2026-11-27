"""Paths, constants and settings shared by every Coastwatch command."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"  # untouched API responses (cache)
PROCESSED_DIR = DATA_DIR / "processed"  # normalised data the map reads
REFERENCE_DIR = DATA_DIR / "reference"  # boundaries
MAPS_DIR = PROJECT_ROOT / "maps"

# Marine Regions gazetteer ID for the Bangladeshi Exclusive Economic Zone.
# https://www.marineregions.org/gazetteer.php?p=details&id=8481
BANGLADESH_EEZ_MRGID = 8481
EEZ_FILE = REFERENCE_DIR / "eez_bangladesh.geojson"

GFW_BASE_URL = "https://gateway.api.globalfishingwatch.org"
GFW_EEZ_DATASET = "public-eez-areas"
PROBE_FILE = DATA_DIR / "probe.json"  # what `coastwatch probe` confirmed


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, no dependency needed.
    Variables already set in the environment win over the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def gfw_token() -> str:
    load_dotenv()
    token = os.environ.get("GFW_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "GFW_TOKEN is not set.\n"
            "  1. Sign up: https://globalfishingwatch.org/our-apis/tokens/signup\n"
            "  2. Create a token: https://globalfishingwatch.org/our-apis/tokens\n"
            "  3. Put it in .env as GFW_TOKEN=..."
        )
    return token
