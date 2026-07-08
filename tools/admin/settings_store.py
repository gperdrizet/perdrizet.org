"""Runtime settings and encrypted secret storage for admin onboarding."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml
from cryptography.fernet import Fernet, InvalidToken

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTENT_ROOT = REPO_ROOT / "data" / "user"
PROFILE_PATH = CONTENT_ROOT / "profile.yaml"
LEGACY_PROFILE_PATH = CONTENT_ROOT / "config.yaml"
PROJECTS_PATH = CONTENT_ROOT / "projects.yaml"
RUNTIME_PATH = CONTENT_ROOT / "runtime.json"
SECRETS_PATH = CONTENT_ROOT / "secrets.enc.json"


DEFAULT_PROFILE: dict[str, Any] = {
    "personal": {
        "name": "",
        "tagline": "",
        "domain": "",
        "email": "",
        "github_username": "",
        "social": {
            "linkedin": "",
            "twitter": "",
            "bluesky": "",
            "substack": "",
            "github": "",
        },
    },
    "bio": {
        "short": "",
        "long": "",
    },
    "home_sections": [],
    "about_sections": [],
}

DEFAULT_RUNTIME: dict[str, Any] = {
    "github": {
        "username": "",
    },
    "llm": {
        "base_url": "http://localhost:8080/v1",
        "model": "llama-3.1-8b",
    },
    "sync": {
        "skip": [],
        "groups": [],
    },
    "setup": {
        "initialized": False,
        "last_bootstrap_at": "",
        "last_sync_at": "",
    },
}


class SecretDecryptError(RuntimeError):
    """Raised when encrypted secrets cannot be decrypted."""


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_profile() -> dict[str, Any]:
    if not PROFILE_PATH.exists() and LEGACY_PROFILE_PATH.exists():
        legacy = yaml.safe_load(LEGACY_PROFILE_PATH.read_text(encoding="utf-8"))
        if isinstance(legacy, dict):
            write_profile(_deep_merge(DEFAULT_PROFILE, legacy))
    if not PROFILE_PATH.exists():
        return dict(DEFAULT_PROFILE)
    data = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return dict(DEFAULT_PROFILE)
    return _deep_merge(DEFAULT_PROFILE, data)


def write_profile(profile: dict[str, Any]) -> None:
    _ensure_parent(PROFILE_PATH)
    PROFILE_PATH.write_text(
        yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def ensure_projects_file() -> None:
    if PROJECTS_PATH.exists():
        return
    _ensure_parent(PROJECTS_PATH)
    PROJECTS_PATH.write_text("projects:\n", encoding="utf-8")


def read_runtime() -> dict[str, Any]:
    if not RUNTIME_PATH.exists():
        return dict(DEFAULT_RUNTIME)
    try:
        data = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(DEFAULT_RUNTIME)
    if not isinstance(data, dict):
        return dict(DEFAULT_RUNTIME)
    return _deep_merge(DEFAULT_RUNTIME, data)


def write_runtime(runtime: dict[str, Any]) -> None:
    _ensure_parent(RUNTIME_PATH)
    RUNTIME_PATH.write_text(json.dumps(runtime, indent=2), encoding="utf-8")


def _derive_fernet(password: str, salt: bytes) -> Fernet:
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def read_secrets(admin_password: str) -> dict[str, str]:
    if not SECRETS_PATH.exists():
        return {}
    payload = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    salt_b64 = payload.get("salt", "")
    token = payload.get("ciphertext", "")
    if not salt_b64 or not token:
        return {}
    salt = base64.b64decode(salt_b64.encode("ascii"))
    fernet = _derive_fernet(admin_password, salt)
    try:
        raw = fernet.decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        raise SecretDecryptError("Could not decrypt secrets with current admin password") from exc
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items()}


def write_secrets(admin_password: str, updates: dict[str, str | None]) -> dict[str, str]:
    current = read_secrets(admin_password)
    for key, value in updates.items():
        if value is None:
            continue
        if value == "":
            current.pop(key, None)
        else:
            current[key] = value

    salt = os.urandom(16)
    fernet = _derive_fernet(admin_password, salt)
    plaintext = json.dumps(current).encode("utf-8")
    token = fernet.encrypt(plaintext).decode("ascii")

    payload = {
        "version": 1,
        "salt": base64.b64encode(salt).decode("ascii"),
        "ciphertext": token,
    }
    _ensure_parent(SECRETS_PATH)
    SECRETS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return current


def mask_secrets(secret_map: dict[str, str]) -> dict[str, str]:
    masked: dict[str, str] = {}
    for key, value in secret_map.items():
        if not value:
            masked[key] = ""
        elif len(value) <= 4:
            masked[key] = "****"
        else:
            masked[key] = f"****{value[-4:]}"
    return masked
