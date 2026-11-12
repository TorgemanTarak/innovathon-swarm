"""HTTP transport for the SWARM registry, rollout controls and local dashboard."""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from swarm.models import (
    Candidate,
    CommandAck,
    Heartbeat,
    IssueReport,
    Outcome,
    PauseRequest,
    Recipe,
    Registration,
    TargetRequest,
    Work,
)
from swarm.registry import Registry, RegistryError


def create_app(db_path: Path | str, token: str | None = None, demo_mode: bool = False) -> FastAPI:
    registry = Registry(db_path)
    app = FastAPI(title="SWARM coordinator", version="0.1.0")
    app.state.registry = registry

    @app.exception_handler(RegistryError)
    async def registry_error(_request: Request, exc: RegistryError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    def authenticate(x_swarm_token: str | None = Header(default=None)) -> None:
        if token is not None and (
            x_swarm_token is None or not secrets.compare_digest(x_swarm_token, token)
        ):
            raise HTTPException(401, "Enter the swarm token to access this coordinator")

    def require_demo() -> None:
        if not demo_mode:
            raise HTTPException(403, "Demo controls are disabled")

    api = APIRouter(prefix="/api", dependencies=[Depends(authenticate)])

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "service": "swarm-coordinator"}

    @api.post("/agents/register")
    def register(payload: Registration) -> dict:
        registry.register(payload)
        return {"ok": True}

    @api.post("/agents/{agent_id}/heartbeat")
    def heartbeat(agent_id: str, payload: Heartbeat) -> dict:
        registry.heartbeat(agent_id, payload)
        return {"ok": True}

    @api.post("/incidents")
    def incident(payload: IssueReport) -> dict:
        registry.report_incident(payload)
        return {"ok": True}

    @api.post("/candidates")
    def candidate(payload: Candidate) -> Recipe:
        return registry.propose(payload)

    @api.get("/agents/{agent_id}/work")
    def work(agent_id: str) -> Work:
        return registry.work(agent_id)

    @api.post("/outcomes")
    def outcome(payload: Outcome) -> dict:
        registry.record_outcome(payload)
        return {"ok": True}

    @api.post("/agents/{agent_id}/commands/ack")
    def ack(agent_id: str, payload: CommandAck) -> dict:
        registry.acknowledge(agent_id, payload)
        return {"ok": True}

    @api.get("/state")
    def state() -> dict:
        return registry.snapshot(demo_mode)

    @api.post("/control/pause")
    def pause(payload: PauseRequest) -> dict:
        registry.pause(payload.paused)
        return {"paused": payload.paused}

    @api.post("/demo/fault", dependencies=[Depends(require_demo)])
    def fault(payload: TargetRequest) -> dict:
        registry.enqueue("inject_fault", payload.agent_ids)
        return {"ok": True}

    @api.post("/demo/repair", dependencies=[Depends(require_demo)])
    def repair(payload: TargetRequest) -> dict:
        registry.enqueue("manual_repair", payload.agent_ids)
        return {"ok": True}

    @api.post("/demo/fail-verification", dependencies=[Depends(require_demo)])
    def fail_verification(payload: TargetRequest) -> dict:
        registry.enqueue("fail_next_verification", payload.agent_ids)
        return {"ok": True}

    @api.post("/demo/reset", dependencies=[Depends(require_demo)])
    def reset() -> dict:
        registry.reset()
        return {"ok": True}

    app.include_router(api)
    web_dir = Path(__file__).parent / "web"
    if web_dir.is_dir():
        app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard() -> Response:
        page = web_dir / "index.html"
        if page.exists():
            return FileResponse(page, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"service": "SWARM", "dashboard": "Not installed", "api": "/api/state"})

    @app.get("/mock", include_in_schema=False)
    def mock_dashboard() -> Response:
        """A browser-only synthetic topology; never reads or mutates fleet state."""
        page = web_dir / "mock.html"
        if page.exists():
            return FileResponse(page, headers={"Cache-Control": "no-cache"})
        raise HTTPException(404, "Mock dashboard not installed")

    return app
