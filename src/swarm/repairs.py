"""Small, preinstalled repair primitives restricted to a marked demo directory.

No path or shell command is accepted from a recipe. These operations never touch
system services, browser profiles, or arbitrary user-selected cache locations.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from uuid import uuid4

MARKER = ".swarm-demo-root"
MAX_CACHE_BYTES = 1024 * 1024
DEFAULT_CACHE = {
    "schema": 1,
    "projects": [
        {"name": "Innovathon launch", "status": "Ready"},
        {"name": "Device intelligence", "status": "Ready"},
    ],
}


class UnsafeDemoPath(ValueError):
    """A requested operation would escape or overwrite an unowned location."""


class InvalidCache(ValueError):
    """The demo application's cache cannot support its normal operation."""


def initialize_root(data_dir: Path | str) -> Path:
    requested = Path(data_dir).expanduser().absolute()
    if requested.is_symlink():
        raise UnsafeDemoPath("The demo data directory must not be a symlink")
    requested.mkdir(parents=True, exist_ok=True)
    root = requested.resolve()
    marker = root / MARKER
    if marker.is_symlink():
        raise UnsafeDemoPath("Invalid demo directory marker")
    if not marker.exists():
        # Never adopt an existing directory full of somebody else's files.
        if any(root.iterdir()):
            raise UnsafeDemoPath("Use a new empty directory or an existing marked SWARM directory")
        try:
            with marker.open("x") as handle:
                handle.write("swarm-demo-v1\n")
        except FileExistsError:  # Concurrent app/agent initialization.
            pass
    validate_root(root)
    for name in ("cache", "quarantine", "logs"):
        directory = scoped_path(root, name)
        directory.mkdir(exist_ok=True)
    return root


def validate_root(root: Path | str) -> Path:
    path = Path(root).expanduser().absolute()
    marker = path / MARKER
    if path.is_symlink() or not path.is_dir() or marker.is_symlink():
        raise UnsafeDemoPath("Unsafe demo directory")
    try:
        valid = marker.is_file() and marker.read_text() == "swarm-demo-v1\n"
    except OSError:
        valid = False
    if not valid:
        raise UnsafeDemoPath("A valid SWARM demo marker is required")
    return path.resolve()


def scoped_path(root: Path | str, relative: str) -> Path:
    base = validate_root(root)
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise UnsafeDemoPath("Only paths inside the demo directory are allowed")
    target = base / rel
    cursor = base
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise UnsafeDemoPath("Symlinks are not allowed in demo resources")
    if not target.resolve().is_relative_to(base):
        raise UnsafeDemoPath("Demo resource escaped its root")
    return target


def cache_path(root: Path | str) -> Path:
    return scoped_path(root, "cache/workspace.json")


def _read_cache(root: Path | str) -> bytes:
    path = cache_path(root)
    # O_NOFOLLOW closes the most common symlink race between the check and open.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise UnsafeDemoPath("Cache must be a regular file")
        data = handle.read(MAX_CACHE_BYTES + 1)
    if len(data) > MAX_CACHE_BYTES:
        raise InvalidCache("Cache exceeds the demo size limit")
    return data


def _validate_cache(data: bytes) -> dict:
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise InvalidCache("Workspace cache is not valid JSON") from exc
    if (
        not isinstance(value, dict)
        or type(value.get("schema")) is not int
        or value["schema"] != 1
        or not isinstance(value.get("projects"), list)
        or len(value["projects"]) > 100
        or not all(isinstance(item, dict) for item in value["projects"])
    ):
        raise InvalidCache("Workspace cache does not match schema 1")
    return value


def write_cache(root: Path | str, data: bytes) -> None:
    path = cache_path(root)
    directory = scoped_path(root, "cache")
    directory.mkdir(exist_ok=True)
    temporary = scoped_path(root, f"cache/.write-{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        cache_path(root)  # Recheck before atomic replacement.
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_cache(root: Path | str) -> dict:
    """Read the actual app cache, regenerating only when it is absent."""
    try:
        return _validate_cache(_read_cache(root))
    except FileNotFoundError:
        write_cache(root, json.dumps(DEFAULT_CACHE).encode())
        return _validate_cache(_read_cache(root))


def cache_is_invalid(root: Path | str) -> bool:
    try:
        _validate_cache(_read_cache(root))
    except InvalidCache:
        return True
    except FileNotFoundError:
        return False
    return False


def fingerprint(root: Path | str) -> str:
    """A local observation fingerprint; raw cache content is never uploaded."""
    try:
        data = _read_cache(root)
    except FileNotFoundError:
        return "missing"
    except InvalidCache:
        return "oversized"
    return hashlib.sha256(data).hexdigest()


def inject_fault(root: Path | str) -> None:
    write_cache(root, b'{"schema": 1, "projects": [CORRUPTED CACHE')


def quarantine_cache(root: Path | str, operation_id: str | None = None) -> Path | None:
    path = cache_path(root)
    if not path.exists():
        return None
    identity = operation_id or uuid4().hex
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
        raise UnsafeDemoPath("Invalid operation identifier")
    destination = scoped_path(root, f"quarantine/{identity}.json")
    if destination.exists():
        raise UnsafeDemoPath("Quarantine destination already exists")
    os.replace(path, destination)
    return destination


def rollback_cache(root: Path | str, backup: Path) -> None:
    base = validate_root(root)
    relative = Path(backup).absolute().relative_to(base)
    if relative.parent != Path("quarantine"):
        raise UnsafeDemoPath("Rollback requires a quarantined cache")
    source = scoped_path(base, str(relative))
    target = cache_path(base)
    if not source.is_file():
        raise UnsafeDemoPath("Rollback backup is missing")
    os.replace(source, target)


def set_verification_fault(root: Path | str, enabled: bool) -> None:
    marker = scoped_path(root, ".fail-verification")
    if enabled:
        marker.write_text("deliberate verification failure\n")
    else:
        marker.unlink(missing_ok=True)


def reset_demo(root: Path | str) -> None:
    """Reset only the known app resources; preserve journal and quarantine audit."""
    validate_root(root)
    set_verification_fault(root, False)
    write_cache(root, json.dumps(DEFAULT_CACHE).encode())
