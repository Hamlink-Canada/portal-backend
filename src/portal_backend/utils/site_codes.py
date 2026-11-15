"""Load and validate the deployment's country-to-Site Code mapping."""

import json
from functools import lru_cache
from pathlib import Path

from rpt_common.utils.regex import COUNTRY_CODE_RE, SITE_CODE_RE

DEFAULT_SITE_CODES_PATH = Path(__file__).parents[1] / "sitecodes.json"


@lru_cache(maxsize=8)
def _load(path: str):
    with Path(path).open(encoding="utf-8") as source:
        mapping = json.load(source)
    if not isinstance(mapping, dict):
        raise ValueError("Site Code mapping must be a JSON object")

    normalized = {}
    for country, entry in mapping.items():
        country = str(country).upper()
        if not COUNTRY_CODE_RE.fullmatch(country) or not isinstance(entry, dict):
            raise ValueError("Site Code mapping contains an invalid country entry")
        codes = entry.get("sitecodes")
        if (
            not isinstance(codes, list)
            or not codes
            or any(not isinstance(code, str) or not SITE_CODE_RE.fullmatch(code) for code in codes)
            or len(set(codes)) != len(codes)
        ):
            raise ValueError(f"Site Code mapping for {country} is invalid")
        normalized[country] = tuple(codes)
    return normalized


def site_codes_for_country(country_code, path=None):
    """Return configured codes, preserving the legacy reserved fallback 25."""
    if not isinstance(country_code, str) or not COUNTRY_CODE_RE.fullmatch(country_code.upper()):
        raise ValueError("invalid country code")
    mapping = _load(str(Path(path) if path else DEFAULT_SITE_CODES_PATH))
    return mapping.get(country_code.upper(), ("25",))
