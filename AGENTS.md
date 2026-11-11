# Development guide

SWARM is a Mac-first Innovathon prototype. Keep the core runnable without a model, cloud account, sudo, Docker or Node build.

- Use `uv` and the committed `uv.lock` for Python dependencies.
- Run `make check` before publishing changes: Ruff, formatting, mypy and pytest.
- Keep wire contracts in `src/swarm/models.py` consistent across endpoint, coordinator and dashboard.
- Share only typed repair identifiers, applicability and bounded evidence; never arbitrary executable commands.
- All repair mutations must stay within a marked demo data directory.
- Verify recovery by exercising `/work`, not merely `/health` or absence of new errors.
- Model explanations are advisory; deterministic code owns authorization and execution.
- Add behavioral tests for changes to rollout, idempotency, learning, rollback or containment.
- Keep OS-specific integrations explicit. Do not claim Windows WER/Remote Actions or TMax inference is implemented/tested unless it is.
- Do not add telemetry, paid API calls or automatic model downloads to the default demo.
