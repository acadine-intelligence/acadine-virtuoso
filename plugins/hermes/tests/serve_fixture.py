"""Loopback-only synthetic browser fixture. Never uses a configured learner workspace."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
import tempfile

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from virtuoso.workspace import WorkspaceService
import uvicorn


def create_app():
    plugin = Path(__file__).resolve().parents[1]
    temp = tempfile.TemporaryDirectory(prefix="virtuoso-desktop-test-")
    home = Path(temp.name).resolve()
    spec = importlib.util.spec_from_file_location("fixture_virtuoso_api", plugin / "dashboard/plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)
    state = {}

    def reset():
        import uuid
        workspace = WorkspaceService.init(home / uuid.uuid4().hex)
        workspace.add_item(
            item_id="retrieval", title="Study retrieval", focus="Learning science",
            entry_mode="learn-first",
            learning_unit="Practising retrieval strengthens later access to a memory. Try recalling a concept before rereading it.",
            prompt="Why does retrieval practice help?",
            answer="Retrieval strengthens later access to a memory.",
            hint="Think about later access.", follow_up="Give one example from a coding project.",
        )
        state["workspace"] = workspace
        return {"synthetic": True}

    reset()
    api.configuration = lambda: (home, {
        "workspace": str(state["workspace"].root),
        "executable": str(Path(sys.executable).parent / "virtuoso"),
    })
    app = FastAPI()
    app.state.fixture_temp = temp
    app.include_router(api.router, prefix="/api/plugins/virtuoso")
    app.post("/test/reset")(reset)

    @app.get("/test/evidence")
    def evidence():
        workspace = state["workspace"]
        return {"synthetic": True, "study": workspace.list_study_events(), "attempts": workspace.list_attempts(), "proposals": workspace.list_proposals(), "skips": workspace.list_review_skips()}

    app.mount("/", StaticFiles(directory=plugin / "dist/fixture", html=True))
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)
