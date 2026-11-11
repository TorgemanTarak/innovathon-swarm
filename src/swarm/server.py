"""Uvicorn development entry point: uvicorn swarm.server:app --reload."""

import os
from pathlib import Path

from swarm.coordinator import create_app

app = create_app(
    Path(os.getenv("SWARM_DB", ".swarm/dev-coordinator.sqlite")),
    token=os.getenv("SWARM_TOKEN") or None,
    demo_mode=os.getenv("SWARM_DEMO_MODE") == "1",
)
