import pytest

from swarm.cli import _agent_args, _check_bind, parser


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


def test_node_agent_uses_expanded_home_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    args = parser().parse_args(["node", "--id", "test", "--data-dir", "~/swarm-node"])

    child_args = _agent_args(args, demo_url="http://127.0.0.1:8771")

    assert child_args[child_args.index("--data-dir") + 1] == str(tmp_path / "swarm-node")


def test_disabled_child_model_settings_override_inherited_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("SWARM_LLM_MODEL", "tmax-local")
    args = parser().parse_args(["node", "--id", "test", "--data-dir", str(tmp_path)])
    args.llm_base_url = None
    args.llm_model = None

    child_args = parser().parse_args(
        _agent_args(args, demo_url="http://127.0.0.1:8771", mode="simulated")
    )

    assert not child_args.llm_base_url
    assert not child_args.llm_model


def test_enabled_child_model_settings_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("SWARM_LLM_MODEL", "tmax-local")
    args = parser().parse_args(["node", "--id", "test", "--data-dir", str(tmp_path)])

    child_args = parser().parse_args(_agent_args(args, demo_url="http://127.0.0.1:8771"))

    assert child_args.llm_base_url == args.llm_base_url
    assert child_args.llm_model == args.llm_model
