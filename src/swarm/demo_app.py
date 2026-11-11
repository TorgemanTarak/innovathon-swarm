"""A real, deliberately breakable cache-backed application for the SWARM demo.

The app regenerates a missing cache. A malformed cache makes the *functional*
operation fail; liveness alone is deliberately insufficient to verify recovery.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from swarm.models import now_iso
from swarm.repairs import InvalidCache, ensure_cache, initialize_root, scoped_path

_LOG_LIMIT = 1_048_576


def _append_log(root: Path, event: str, version: str, error_code: str | None = None) -> None:
    """Keep a bounded local log without following redirected log paths."""
    directory = scoped_path(root, "logs")
    target = scoped_path(root, "logs/app.jsonl")
    previous = scoped_path(root, "logs/app.jsonl.1")
    if directory.is_symlink() or target.is_symlink() or previous.is_symlink():
        raise ValueError("Demo log paths must not be symbolic links")
    directory.mkdir(exist_ok=True)
    if target.exists() and target.stat().st_size >= _LOG_LIMIT:
        target.replace(previous)
    record = {
        "timestamp": now_iso(),
        "event": event,
        "app_id": "workspace-demo",
        "app_version": version,
        "error_code": error_code,
    }
    with target.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, separators=(",", ":")) + "\n")


def create_app(data_dir: Path | str, app_version: str = "1.0.0") -> FastAPI:
    root = initialize_root(Path(data_dir))
    app = FastAPI(title="SWARM Workspace Demo", version=app_version)
    app.state.data_dir = root

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "kind": "liveness", "app_version": app_version}

    @app.get("/work")
    def work() -> JSONResponse:
        marker = root / ".fail-verification"
        if marker.exists() or marker.is_symlink():
            _append_log(root, "workspace.load.failed", app_version, "VERIFICATION_FAILED")
            return JSONResponse(
                status_code=503,
                content={
                    "ok": False,
                    "error_code": "VERIFICATION_FAILED",
                    "message": "The demo's functional verification fault is active.",
                },
                headers={"Cache-Control": "no-store"},
            )
        try:
            cache = ensure_cache(root)
        except InvalidCache:
            _append_log(root, "workspace.load.failed", app_version, "CACHE_INVALID")
            return JSONResponse(
                status_code=503,
                content={
                    "ok": False,
                    "error_code": "CACHE_INVALID",
                    "message": "The workspace cache cannot be read. Workspace loading is unavailable.",
                },
                headers={"Cache-Control": "no-store"},
            )
        except (OSError, ValueError):
            _append_log(root, "workspace.load.failed", app_version, "STORAGE_UNAVAILABLE")
            return JSONResponse(
                status_code=503,
                content={
                    "ok": False,
                    "error_code": "STORAGE_UNAVAILABLE",
                    "message": "The demo storage is unavailable or does not satisfy its path policy.",
                },
                headers={"Cache-Control": "no-store"},
            )
        _append_log(root, "workspace.load.succeeded", app_version)
        return JSONResponse(
            content={"ok": True, "projects": cache["projects"], "app_version": app_version},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _HTML.replace("__APP_VERSION__", html.escape(app_version))

    return app


_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Workspace · SWARM demo</title>
<style>
:root{color-scheme:light;--bg:#EEF3F7;--panel:#fff;--line:#D8DCEA;--ink:#221F20;--muted:#566579;--blue:#1046A8;--teal:#00B1A9;--success:#00776f;--papaya:#FF7461;--error:#a6382b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 'Proxima Nova','Segoe UI',Arial,sans-serif}
button,input{font:inherit}
button{cursor:pointer}
button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #3194F7;outline-offset:3px}
header{padding:18px max(32px,calc((100% - 1016px)/2));display:flex;align-items:center;justify-content:space-between;gap:16px;background:#0C5497;color:#fff;border-bottom:1px solid #1046A8}
.brand{display:flex;gap:10px;align-items:center;font-size:20px;font-weight:600}
.mark{display:grid;place-items:center;width:32px;height:32px;color:#fff;border:1px solid #ffffff80;border-radius:4px}
.tag,.eyebrow{font-size:11px;font-weight:600;letter-spacing:.8px;text-transform:uppercase}
.tag{color:#fff}
main{max-width:1080px;margin:auto;padding:36px 32px}
.eyebrow{color:var(--blue);margin:0 0 8px}
h1{font-size:clamp(28px,4vw,38px);line-height:1.2;font-weight:600;letter-spacing:-.6px;margin:0 0 16px}
.intro{color:var(--muted);max-width:600px;margin:0 0 24px}
.toolbar{display:flex;gap:22px;align-items:center;flex-wrap:wrap}
button{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--blue);background:var(--blue);color:#fff;padding:9px 18px;border-radius:4px;font-weight:600}
button:hover:not(:disabled){background:#0B3580;border-color:#0B3580}
button:disabled{opacity:.6;cursor:wait}
label{display:flex;gap:8px;align-items:center;color:var(--muted);font-size:13px}
input{accent-color:var(--blue);width:16px;height:16px}
.status{display:flex;gap:11px;align-items:center;background:var(--panel);border:1px solid var(--line);border-left:3px solid #647893;border-radius:4px;padding:13px 16px;margin:24px 0 20px}
.dot{height:8px;width:8px;border-radius:50%;background:var(--muted);flex:none}
.status[data-state=healthy]{border-left-color:var(--teal);background:#F1FAF9}
.status[data-state=healthy] .dot{background:var(--teal)}
.status[data-state=healthy] strong{color:var(--success)}
.status[data-state=error]{border-left-color:var(--papaya);background:#FFF4F1}
.status[data-state=error] .dot{background:var(--papaya)}
.status[data-state=error] strong{color:var(--error)}
.status strong{font-size:13px;font-weight:600}
.status span:last-child{margin-left:auto;color:var(--muted);font-size:11px;white-space:nowrap}
.projects{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,235px),1fr));gap:16px;min-height:150px}
.project{background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:24px}
.project .icon{display:grid;place-items:center;width:36px;height:36px;font-size:22px;color:var(--blue);background:#EEF5FC;border-radius:4px;margin-bottom:20px}
.project h2{margin:0 0 7px;font-size:18px;font-weight:600;letter-spacing:-.2px}
.project p{margin:0;color:var(--muted);font-size:13px}
.empty{padding:24px;background:var(--panel);border:1px solid var(--line);border-radius:4px;color:var(--muted);grid-column:1/-1;margin:0}
.empty h2{color:var(--error);font-size:22px;font-weight:600;margin:0 0 8px}
.empty p{margin:0}
.details{border-top:1px solid var(--line);margin-top:32px;padding-top:20px;display:grid;grid-template-columns:1fr 1fr;gap:24px;color:var(--muted);font-size:12px}
.details strong{display:block;color:var(--ink);font-size:12px;font-weight:600;margin-bottom:5px}
.mono{font-family:ui-monospace,SFMono-Regular,monospace}
footer{max-width:1080px;margin:0 auto;padding:12px 32px 28px;color:var(--muted);font-size:11px}
@media(max-width:600px){header{padding:18px 20px;flex-wrap:wrap;gap:8px}.tag{font-size:10px;letter-spacing:.4px}main{padding:28px 20px}.details{grid-template-columns:1fr}.status span:last-child{display:none}footer{padding:12px 20px 24px}}
</style>
</head>
<body>
<header><div class="brand"><span class="mark" aria-hidden="true">◫</span>Workspace</div><span class="tag">SWARM demo application</span></header>
<main>
<p class="eyebrow">Your work, in one place</p>
<h1>Make room for<br>your next idea.</h1>
<p class="intro">Load your projects from this device's workspace cache. Every request below runs the real application operation.</p>
<div class="toolbar"><button id="load" type="button">Load workspace <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 12 12 4M4 4h8v8"/></svg></button><label><input id="auto" type="checkbox" checked> Refresh every 3 seconds</label></div>
<div id="status" class="status" data-state="unknown" role="status" aria-live="polite"><span class="dot" aria-hidden="true"></span><strong id="status-text">Waiting for first request</strong><span id="request-time">—</span></div>
<div id="projects" class="projects" aria-live="polite"><p class="empty">Projects will appear when the workspace loads.</p></div>
<div class="details"><div><strong>Functional health check</strong><span class="mono">GET /work</span> reads and validates the cache. A live process can still return a failed operation.</div><div><strong>Controlled failure, real recovery</strong>The SWARM agent watches this app. Renaming its corrupted cache allows it to regenerate on the next request.</div></div>
</main><footer>Workspace demo v__APP_VERSION__ · Local application data · No production applications are modified</footer>
<script>
'use strict';
const button=document.getElementById('load');
const box=document.getElementById('projects');
const status=document.getElementById('status');
let busy=false, previousState='';
function element(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
async function load(){
 if(busy)return;busy=true;button.disabled=true;const start=performance.now();
 try{
  const response=await fetch('/work',{cache:'no-store'});const result=await response.json();
  const ok=response.ok&&result.ok===true;
  status.dataset.state=ok?'healthy':'error';
  document.getElementById('status-text').textContent=ok?'Workspace is ready':(result.error_code||'Workspace unavailable');
  document.getElementById('request-time').textContent=`${response.status} · ${Math.round(performance.now()-start)} ms · ${new Date().toLocaleTimeString()}`;
  const state=JSON.stringify({ok,projects:result.projects,message:result.message});
  if(state!==previousState){previousState=state;box.replaceChildren();
   if(ok){for(const project of result.projects){const card=element('article',undefined,'project');card.append(element('div','◈','icon'),element('h2',typeof project.name==='string'?project.name:'Project'),element('p',typeof project.status==='string'?project.status:'Ready'));box.append(card);}if(!result.projects.length)box.append(element('p','No projects in this workspace.','empty'));}
   else{const empty=element('div',undefined,'empty');empty.append(element('h2','Workspace could not load.'),element('p',result.message||'The application operation failed.'));box.append(empty);}
  }
 }catch(error){status.dataset.state='error';document.getElementById('status-text').textContent='Application connection unavailable';document.getElementById('request-time').textContent='Request failed';}
 finally{busy=false;button.disabled=false;}
}
button.addEventListener('click',load);setInterval(()=>{if(document.getElementById('auto').checked)load();},3000);load();
</script>
</body></html>"""
