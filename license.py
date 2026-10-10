"""CleanTake Pro license check — intentionally minimal.

A license key looks like: CT-PRO-XXXX-XXXX (X = A-Z, 0-9).
The last character is a check digit:
    check = base36( sum( ord(c) * (i+1) for i, c in enumerate(payload7) ) % 36 )
    payload = 7 random chars + check char  (8 chars total)

This is NOT real DRM — it is a friendly gate so the $29 Pro build asks for
a key. Real keys are issued by Lemon Squeezy after purchase; the dev key in
pro/DEV_LICENSE_KEY.txt is for testing only and must never be bundled into
a shipped build.
"""
import os
import random
import re
import string
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(BASE, "license.key")

_ALPHABET = string.ascii_uppercase + string.digits  # base36
_KEY_RE = re.compile(r"^CT-PRO-([A-Z0-9]{4})-([A-Z0-9]{4})$")


def _check_char(payload7: str) -> str:
    total = sum(ord(c) * (i + 1) for i, c in enumerate(payload7))
    return _ALPHABET[total % 36]


def generate_key(rng: random.Random = None) -> str:
    """Generate a new valid license key."""
    rng = rng or random.SystemRandom()
    payload7 = "".join(rng.choice(_ALPHABET) for _ in range(7))
    payload = payload7 + _check_char(payload7)
    return f"CT-PRO-{payload[:4]}-{payload[4:]}"


def validate_key(key: str) -> bool:
    """True if the key has the right shape AND a valid check digit."""
    if not key or not isinstance(key, str):
        return False
    key = key.strip().upper()
    m = _KEY_RE.match(key)
    if not m:
        return False
    payload = m.group(1) + m.group(2)
    return payload[-1] == _check_char(payload[:7])


def read_saved_key() -> str:
    """Read license.key next to the exe (or repo root in dev)."""
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_key(key: str) -> None:
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key.strip().upper() + "\n")


def is_licensed() -> bool:
    return validate_key(read_saved_key())


if __name__ == "__main__":
    # python license.py --gen  -> print one fresh key (for the key issuer)
    if len(sys.argv) > 1 and sys.argv[1] == "--gen":
        print(generate_key())
    else:
        print("saved key valid:", is_licensed())
