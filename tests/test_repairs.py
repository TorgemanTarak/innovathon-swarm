import json

import pytest

from swarm.repairs import (
    DEFAULT_CACHE,
    InvalidCache,
    UnsafeDemoPath,
    cache_is_invalid,
    cache_path,
    ensure_cache,
    initialize_root,
    inject_fault,
    quarantine_cache,
    reset_demo,
    rollback_cache,
    write_cache,
)


def test_repair_regenerates_cache_and_rollback_restores_original(tmp_path):
    root = initialize_root(tmp_path / "device")
    assert ensure_cache(root) == DEFAULT_CACHE
    inject_fault(root)
    broken = cache_path(root).read_bytes()
    assert cache_is_invalid(root)
    with pytest.raises(InvalidCache):
        ensure_cache(root)
    backup = quarantine_cache(root, "attempt-1")
    assert backup is not None
    assert ensure_cache(root) == DEFAULT_CACHE
    rollback_cache(root, backup)
    assert cache_path(root).read_bytes() == broken
    reset_demo(root)
    assert ensure_cache(root) == DEFAULT_CACHE


def test_unowned_directory_is_not_adopted(tmp_path):
    (tmp_path / "important.txt").write_text("keep me")
    with pytest.raises(UnsafeDemoPath):
        initialize_root(tmp_path)
    assert (tmp_path / "important.txt").read_text() == "keep me"


def test_symlink_cache_cannot_be_repaired_or_overwritten(tmp_path):
    root = initialize_root(tmp_path / "device")
    victim = tmp_path / "important.json"
    victim.write_text("keep me")
    cache_path(root).symlink_to(victim)
    with pytest.raises(UnsafeDemoPath):
        inject_fault(root)
    with pytest.raises(UnsafeDemoPath):
        quarantine_cache(root)
    with pytest.raises(UnsafeDemoPath):
        ensure_cache(root)
    assert victim.read_text() == "keep me"


def test_reset_requires_marker_and_preserves_unrelated_files(tmp_path):
    root = initialize_root(tmp_path / "device")
    unrelated = root / "notes.txt"
    unrelated.write_text("keep")
    reset_demo(root)
    assert unrelated.read_text() == "keep"
    (root / ".swarm-demo-root").unlink()
    with pytest.raises(UnsafeDemoPath):
        reset_demo(root)


@pytest.mark.parametrize("payload", [b'{"schema":true,"projects":[]}', b"x" * (1024 * 1024 + 1)])
def test_schema_and_size_are_checked(tmp_path, payload):
    root = initialize_root(tmp_path / "device")
    write_cache(root, payload)
    with pytest.raises(InvalidCache):
        ensure_cache(root)


def test_quarantine_path_and_directory_links_are_rejected(tmp_path):
    root = initialize_root(tmp_path / "device")
    write_cache(root, json.dumps(DEFAULT_CACHE).encode())
    with pytest.raises(UnsafeDemoPath):
        quarantine_cache(root, "../../escape")
    (root / "quarantine").rmdir()
    (root / "quarantine").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(UnsafeDemoPath):
        quarantine_cache(root)
