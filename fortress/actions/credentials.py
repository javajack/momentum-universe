"""Zerodha credential configuration — write the recipient's own keys to .env.

The repo ships credential-free. This writes/updates a gitignored `.env` so the
live features can authenticate with the RECIPIENT's own Zerodha keys. Nothing
is ever committed (`.env` is gitignored).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

_KEYS = ("ZERODHA_API_KEY", "ZERODHA_API_SECRET")


def _parse_env(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def save_credentials(api_key: str, api_secret: str, env_path: str = ".env") -> Path:
    """Write ZERODHA_API_KEY / ZERODHA_API_SECRET into `env_path`, preserving any
    other variables already present. Returns the path written. Does not echo or
    log the secret. Raises ValueError if either value is blank.
    """
    if not api_key.strip() or not api_secret.strip():
        raise ValueError("both api_key and api_secret are required")
    path = Path(env_path)
    existing = _parse_env(path.read_text()) if path.exists() else {}
    existing["ZERODHA_API_KEY"] = api_key.strip()
    existing["ZERODHA_API_SECRET"] = api_secret.strip()
    lines = [f"{k}={existing[k]}" for k in existing]
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)  # owner-only; it holds a secret
    return path
