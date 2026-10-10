"""CleanTake Pro license check.

Two key kinds are accepted:

1. LEGACY keys: CT-PRO-XXXX-XXXX with a check digit (offline friendly gate,
   e.g. the dev key). Validated locally, no network needed.

2. LEMON SQUEEZY keys: keys issued by Lemon Squeezy after purchase
   (look like "38b1460a-5104-4067-a91d-77b872934d51"). Validated against
   the Lemon Squeezy License API (no API secret needed — these endpoints
   authenticate with the license key itself):
       POST https://api.lemonsqueezy.com/v1/licenses/activate
            {license_key, instance_name} -> {activated, instance: {id, ...}}
       POST https://api.lemonsqueezy.com/v1/licenses/validate
            {license_key, instance_id}   -> {valid, license_key: {status}}
       POST https://api.lemonsqueezy.com/v1/licenses/deactivate
            {license_key, instance_id}   -> {deactivated}

Local state (license.key next to the app, JSON):
    {"kind": "legacy"|"ls", "key": "...", "instance_id": "...",
     "last_checked": <epoch seconds>, "status": "active"}

Rules:
- A saved LS license is re-validated at most once every RECHECK_INTERVAL
  (24h). In between, the cache is trusted — no network on every launch.
- If the network is unreachable, the cached license is trusted (offline
  grace) — a legit user is never locked out by a dead connection.
- A refunded/disabled key stops unlocking on the next successful
  re-validation.
"""
import json
import os
import random
import re
import socket
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(BASE, "license.key")

# --- Lemon Squeezy -----------------------------------------------------------
LS_API = "https://api.lemonsqueezy.com/v1/licenses"
LS_TIMEOUT = 10  # seconds; never hang the UI on a dead connection
RECHECK_INTERVAL = 24 * 3600  # re-validate an LS key at most once a day

# Product scoping (docs recommend hard-coding these so a key bought for some
# other Lemon Squeezy product can't unlock CleanTake). Fill in once the
# Lemon Squeezy product exists; while None the scope check is skipped.
LS_STORE_ID = None  # TODO: fill from LS dashboard Settings
LS_PRODUCT_ID = 1427924
LS_VARIANT_ID = 2230163

# --- Legacy key format -------------------------------------------------------
_ALPHABET = string.ascii_uppercase + string.digits  # base36
_KEY_RE = re.compile(r"^CT-PRO-([A-Z0-9]{4})-([A-Z0-9]{4})$")


class LicenseNetworkError(Exception):
    """The license server could not be reached."""


def _check_char(payload7: str) -> str:
    total = sum(ord(c) * (i + 1) for i, c in enumerate(payload7))
    return _ALPHABET[total % 36]


def generate_key(rng: random.Random = None) -> str:
    """Generate a new valid LEGACY license key."""
    rng = rng or random.SystemRandom()
    payload7 = "".join(rng.choice(_ALPHABET) for _ in range(7))
    payload = payload7 + _check_char(payload7)
    return f"CT-PRO-{payload[:4]}-{payload[4:]}"


def validate_legacy_key(key: str) -> bool:
    """True if the key has the legacy shape AND a valid check digit."""
    if not key or not isinstance(key, str):
        return False
    key = key.strip().upper()
    m = _KEY_RE.match(key)
    if not m:
        return False
    payload = m.group(1) + m.group(2)
    return payload[-1] == _check_char(payload[:7])


# Backwards-compatible alias (cleantake_pro.py used to import this name).
validate_key = validate_legacy_key


# --- HTTP --------------------------------------------------------------------
def _http_post(endpoint: str, data: dict) -> dict:
    """POST form-urlencoded to the LS License API, return parsed JSON."""
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        LS_API + endpoint, data=body, method="POST",
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=LS_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # LS answers 4xx with a JSON body like {"activated": false, "error": ...}
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            raise LicenseNetworkError(f"license server HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise LicenseNetworkError(str(e))


def _instance_name() -> str:
    try:
        return socket.gethostname() or "CleanTake-Pro"
    except OSError:
        return "CleanTake-Pro"


def _scope_ok(meta: dict) -> bool:
    """Reject keys issued for a different store/product (when IDs are set)."""
    if not meta:
        return True
    for attr, mkey in (("LS_STORE_ID", "store_id"),
                       ("LS_PRODUCT_ID", "product_id"),
                       ("LS_VARIANT_ID", "variant_id")):
        want = globals()[attr]
        if want is not None and str(meta.get(mkey)) != str(want):
            return False
    return True


# --- Local state -------------------------------------------------------------
def _load_state() -> dict:
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        state = json.loads(raw)
        return state if isinstance(state, dict) else {}
    except ValueError:
        # Old format: plain-text legacy key, one per file.
        return {"kind": "legacy", "key": raw}


def _save_state(state: dict) -> None:
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def read_saved_key() -> str:
    """The raw key string from license.key (any kind)."""
    return _load_state().get("key", "")


def save_key(key: str) -> None:
    """Backwards-compatible saver: stores a LEGACY key."""
    _save_state({"kind": "legacy", "key": key.strip().upper(),
                 "last_checked": int(time.time()), "status": "active"})


def clear_saved_key() -> None:
    try:
        os.remove(KEY_FILE)
    except OSError:
        pass


# --- Public API --------------------------------------------------------------
def activate_key(key: str):
    """Try to activate `key`. Returns (ok: bool, message: str).

    Legacy keys are accepted offline. Lemon Squeezy keys are activated
    against the LS License API (needs network). Re-entering an already
    saved LS key re-validates the existing instance instead of burning a
    new activation.
    """
    if not key or not isinstance(key, str):
        return False, "Please enter your license key."
    key = key.strip()

    # 1) Legacy format -> offline accept (dev keys, old keys).
    if validate_legacy_key(key):
        _save_state({"kind": "legacy", "key": key.strip().upper(),
                     "last_checked": int(time.time()), "status": "active"})
        return True, "License accepted — Pro unlocked."

    # 2) Same LS key already saved? Re-validate the existing instance
    #    instead of creating a new one (saves activation_limit).
    state = _load_state()
    if (state.get("kind") == "ls"
            and state.get("key", "").strip().lower() == key.strip().lower()
            and state.get("instance_id")):
        ok, msg = _revalidate(state)
        return ok, msg if ok else (
            False, "This key was deactivated or refunded. " + msg)

    # 3) New LS key -> activate online.
    try:
        resp = _http_post("/activate", {
            "license_key": key,
            "instance_name": _instance_name(),
        })
    except LicenseNetworkError:
        return False, ("Couldn't reach the Lemon Squeezy license server — "
                       "check your connection and try again.")

    if resp.get("activated") and resp.get("license_key", {}).get("status") == "active":
        if not _scope_ok(resp.get("meta") or {}):
            return False, "This key was issued for a different product."
        _save_state({
            "kind": "ls",
            "key": key.strip(),
            "instance_id": (resp.get("instance") or {}).get("id", ""),
            "last_checked": int(time.time()),
            "status": "active",
        })
        return True, "License activated — Pro unlocked."

    err = resp.get("error") or "invalid key"
    return False, f"License key not accepted: {err}. Check the key and try again."


def _revalidate(state: dict):
    """Online re-check of a saved LS license. Returns (ok, message)."""
    payload = {"license_key": state["key"]}
    if state.get("instance_id"):
        payload["instance_id"] = state["instance_id"]
    try:
        resp = _http_post("/validate", payload)
    except LicenseNetworkError:
        return True, "offline"  # network dead -> trust the cache
    status = (resp.get("license_key") or {}).get("status", "")
    ok = bool(resp.get("valid")) and status == "active"
    state["status"] = status or ("active" if ok else "unknown")
    state["last_checked"] = int(time.time())
    _save_state(state)
    if ok:
        return True, "License valid."
    return False, f"License status: {state['status']}."


def is_licensed() -> bool:
    """True if the app may run Pro features right now.

    Fast path: saved license re-checked at most once a day; offline always
    trusts the cache so a dead connection never locks out a legit user.
    """
    state = _load_state()
    if not state.get("key"):
        return False
    if state.get("kind") == "legacy":
        return validate_legacy_key(state["key"])
    # Lemon Squeezy key
    if state.get("status") not in (None, "active"):
        return False
    last = state.get("last_checked") or 0
    if time.time() - last < RECHECK_INTERVAL:
        return True
    ok, _ = _revalidate(dict(state))
    return ok


def deactivate_key():
    """Release this machine's LS activation and forget the local key."""
    state = _load_state()
    if state.get("kind") == "ls" and state.get("instance_id"):
        try:
            _http_post("/deactivate", {
                "license_key": state["key"],
                "instance_id": state["instance_id"],
            })
        except LicenseNetworkError:
            pass  # still forget the local copy
    clear_saved_key()
    return True, "License removed from this machine."


if __name__ == "__main__":
    # python license.py --gen  -> print one fresh LEGACY key (dev use only)
    if len(sys.argv) > 1 and sys.argv[1] == "--gen":
        print(generate_key())
    elif len(sys.argv) > 1 and sys.argv[1] == "--deactivate":
        print(deactivate_key()[1])
    else:
        print("saved key licensed:", is_licensed())
