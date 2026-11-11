import pytest

from swarm.cli import _check_bind, parser


def test_lan_binding_requires_explicit_token():
    _check_bind("127.0.0.1", None)
    _check_bind("0.0.0.0", "a-token-long-enough-for-demo")
    with pytest.raises(ValueError, match="SWARM_TOKEN"):
        _check_bind("0.0.0.0", None)


def test_demo_defaults_are_native_processes_without_cloud_dependencies():
    args = parser().parse_args(["demo"])
    assert args.nodes == 3
    assert args.port == 8765


def test_node_identity_and_data_root_are_explicit():
    with pytest.raises(SystemExit):
        parser().parse_args(["node"])
