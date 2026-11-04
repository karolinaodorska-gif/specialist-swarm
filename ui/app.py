"""
Local dashboard for the Deal Desk swarm: swarm graph + event timeline.

    python ui/app.py            # -> http://127.0.0.1:8000

Three modes (all stream normalized events to the browser via SSE):
  live    start a real session against the coordinator (needs API key + IDs)
  replay  replay an earlier session by ID (reads events, no new run, no cost)
  demo    scripted sample run, no API needed (for building / showing the UI)

Live runs use run_deal_desk.create_session / build_user_message (same prompt and file
mounts as the CLI) and the CRM handler from crm-mcp/.
Run from anywhere: the app switches to the repo root (scripts use relative paths).
"""

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "crm-mcp"))
sys.path.insert(0, str(ROOT / "ui"))

import uvicorn  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.exceptions import HTTPException  # noqa: E402
from starlette.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

import custom_tool  # noqa: E402
import demo  # noqa: E402

load_dotenv(ROOT / ".env")

COORDINATOR_NAME = "Deal Desk Senior Partner"
OUTPUT_DIR = ROOT / "outputs"
BETA = "managed-agents-2026-04-01"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,128}$")
_live_lock = threading.Lock()


# ---------------------------------------------------------------- helpers --

def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def live_readiness() -> dict:
    missing = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY nicht gesetzt")
    if not (ROOT / ".coordinator_id").exists():
        missing.append(".coordinator_id fehlt (create_coordinator.py ausführen)")
    if not (ROOT / ".environment_id").exists():
        missing.append(".environment_id fehlt (setup_environment.py ausführen)")
    return {"ready": not missing, "missing": missing}


def _client():
    from anthropic import Anthropic

    return Anthropic()


class Normalizer:
    """Turns raw SDK events into the small, stable schema the browser draws."""

    def __init__(self):
        self.threads: dict[str, str] = {}  # session_thread_id -> agent name

    def agent_of(self, event) -> str:
        tid = getattr(event, "session_thread_id", None)
        return self.threads.get(tid, COORDINATOR_NAME) if tid else COORDINATOR_NAME

    def __call__(self, event) -> list[dict]:
        t = event.type
        if t == "session.thread_created":
            self.threads[event.session_thread_id] = event.agent_name
            return [{"kind": "thread_created", "agent": event.agent_name}]
        if t in ("session.thread_status_running", "session.thread_status_idle",
                 "session.thread_status_terminated"):
            name = getattr(event, "agent_name", None) or self.threads.get(
                getattr(event, "session_thread_id", ""), "?")
            state = t.rsplit("_", 1)[1]
            return [{"kind": "thread_status", "agent": name, "state": state}]
        if t == "session.status_running":
            return [{"kind": "thread_status", "agent": COORDINATOR_NAME, "state": "running"}]
        if t == "agent.thread_message_sent":
            text = "".join(getattr(b, "text", "") for b in getattr(event, "content", []))
            return [{"kind": "delegate", "agent": getattr(event, "to_agent_name", None) or "?",
                     "text": text}]
        if t == "agent.thread_message_received":
            text = "".join(getattr(b, "text", "") for b in getattr(event, "content", []))
            return [{"kind": "reply", "agent": getattr(event, "from_agent_name", None) or "?",
                     "text": text}]
        if t == "agent.message":
            text = "".join(getattr(b, "text", "") for b in event.content
                           if getattr(b, "type", None) == "text")
            return [{"kind": "message", "agent": self.agent_of(event), "text": text}] if text else []
        if t in ("agent.tool_use", "agent.mcp_tool_use"):
            return [{"kind": "tool", "agent": self.agent_of(event),
                     "name": getattr(event, "name", "?")}]
        if t == "session.error":
            return [{"kind": "error", "text": str(getattr(event, "error", event))}]
        return []


def _usage_dict(u) -> dict:
    """SDK usage object -> plain dict. list_cost.amount is in cents (string)."""
    if u is None:
        return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": None}
    cc = getattr(u, "cache_creation", None)
    write = ((getattr(cc, "ephemeral_1h_input_tokens", 0) or 0)
             + (getattr(cc, "ephemeral_5m_input_tokens", 0) or 0)) if cc else 0
    cost = None
    if getattr(u, "list_cost", None) is not None:
        try:
            cost = int(u.list_cost.amount) / 100
        except (TypeError, ValueError):
            cost = None
    return {"input": u.input_tokens or 0, "output": u.output_tokens or 0,
            "cache_read": u.cache_read_input_tokens or 0, "cache_write": write, "cost_usd": cost}


class UsagePoller:
    """Reads token usage (session total + per thread) at most every `interval` seconds."""

    def __init__(self, client, session_id: str, interval: float = 3.0):
        self.client, self.session_id, self.interval = client, session_id, interval
        self._last = 0.0

    def poll(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last < self.interval:
            return None
        self._last = now
        try:
            session = self.client.beta.sessions.retrieve(self.session_id)
            threads = [
                {"agent": getattr(th.agent, "name", None) or "?", **_usage_dict(th.usage)}
                for th in self.client.beta.sessions.threads.list(self.session_id)
            ]
            return {"kind": "usage", "total": _usage_dict(session.usage), "threads": threads}
        except Exception:  # usage is a nice-to-have, never break the run
            return None


def crm_event(event, agent: str, result: str) -> dict:
    return {"kind": "crm_call", "agent": agent, "name": event.name,
            "input": event.input, "result": result}


# ------------------------------------------------------------------ modes --

def run_live():
    # same prompt, file setup and download logic as the CLI
    from run_deal_desk import build_user_message, create_session, download_outputs

    ready = live_readiness()
    if not ready["ready"]:
        yield {"kind": "error", "text": "Live-Lauf nicht möglich: " + "; ".join(ready["missing"])}
        return
    if not _live_lock.acquire(blocking=False):
        yield {"kind": "error", "text": "Es läuft bereits ein Lauf."}
        return
    try:
        client = _client()
        norm = Normalizer()
        session = create_session(client, "Deal Desk — Acme Corp RFP (UI)")
        (ROOT / ".last_session_id").write_text(session.id)
        yield {"kind": "session", "id": session.id}
        poller = UsagePoller(client, session.id)

        message = build_user_message()
        with client.beta.sessions.events.stream(session.id) as stream:
            client.beta.sessions.events.send(
                session.id,
                events=[{"type": "user.message", "content": [{"type": "text", "text": message}]}],
            )
            for event in stream:
                t = event.type
                usage = poller.poll()
                if usage:
                    yield usage
                if t == "agent.custom_tool_use" and custom_tool.is_crm_tool(event.name):
                    result = custom_tool.run_crm_tool(event.name, event.input)
                    reply = {"type": "user.custom_tool_result",
                             "custom_tool_use_id": event.id,
                             "content": [{"type": "text", "text": result}]}
                    if getattr(event, "session_thread_id", None):
                        reply["session_thread_id"] = event.session_thread_id
                    client.beta.sessions.events.send(session.id, events=[reply])
                    yield crm_event(event, norm.agent_of(event), result)
                    continue
                if t == "session.status_idle":
                    if event.stop_reason.type == "requires_action":
                        continue
                    yield {"kind": "thread_status", "agent": COORDINATOR_NAME, "state": "idle"}
                    break
                yield from norm(event)

        final_usage = poller.poll(force=True)
        if final_usage:
            yield final_usage

        yield {"kind": "files", "files": download_outputs(client, session.id, OUTPUT_DIR)}
        yield {"kind": "done", "session": session.id,
               "link": f"https://platform.claude.com/sessions/{session.id}"}
    except (Exception, SystemExit) as exc:  # surface everything in the UI (create_session may SystemExit)
        yield {"kind": "error", "text": f"{type(exc).__name__}: {exc}"}
    finally:
        if _live_lock.locked():
            _live_lock.release()


def run_replay(session_id: str):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        yield {"kind": "error", "text": "ANTHROPIC_API_KEY nicht gesetzt."}
        return
    try:
        client = _client()
        norm = Normalizer()
        yield {"kind": "session", "id": session_id}
        prev = None
        for event in client.beta.sessions.events.list(session_id, order="asc", limit=100):
            ts = getattr(event, "processed_at", None)
            if prev is not None and ts is not None:
                time.sleep(min(max((ts - prev).total_seconds(), 0.05), 1.2))
            prev = ts or prev
            if event.type == "agent.custom_tool_use" and custom_tool.is_crm_tool(event.name):
                yield crm_event(event, norm.agent_of(event),
                                custom_tool.run_crm_tool(event.name, event.input))
                continue
            if event.type == "session.status_idle" and event.stop_reason.type != "requires_action":
                yield {"kind": "thread_status", "agent": COORDINATOR_NAME, "state": "idle"}
                continue
            yield from norm(event)
        final_usage = UsagePoller(client, session_id).poll(force=True)
        if final_usage:
            yield final_usage
        yield {"kind": "done", "session": session_id,
               "link": f"https://platform.claude.com/sessions/{session_id}"}
    except Exception as exc:
        yield {"kind": "error", "text": f"{type(exc).__name__}: {exc}"}


def run_demo():
    for delay, event in demo.script(COORDINATOR_NAME):
        time.sleep(delay)
        yield event


# ----------------------------------------------------------------- routes --

def stream_run(request):
    mode = request.query_params.get("mode", "demo")
    if mode == "live":
        gen = run_live()
    elif mode == "replay":
        sid = request.query_params.get("session_id", "")
        if not SESSION_ID_RE.match(sid):
            raise HTTPException(400, "Ungültige Session-ID")
        gen = run_replay(sid)
    elif mode == "demo":
        gen = run_demo()
    else:
        raise HTTPException(400, "Unbekannter Modus")
    return StreamingResponse(
        (sse(e) for e in gen),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def index(request):
    return FileResponse(ROOT / "ui" / "static" / "index.html")


def status(request):
    last = (ROOT / ".last_session_id")
    ids_file = ROOT / ".specialist_ids.json"
    try:
        has_critic = "critic" in json.loads(ids_file.read_text()) if ids_file.exists() else False
    except ValueError:
        has_critic = False
    return JSONResponse({
        "has_critic": has_critic,
        "live": live_readiness(),
        "last_session_id": last.read_text().strip() if last.exists() else "",
    })


def download(request):
    name = Path(request.path_params["name"]).name  # strip any path components
    path = OUTPUT_DIR / name
    if not path.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(path, filename=name)


app = Starlette(routes=[
    Route("/", index),
    Route("/api/status", status),
    Route("/api/run", stream_run),
    Route("/files/{name}", download),
])

if __name__ == "__main__":
    port = int(os.environ.get("UI_PORT", "8000"))
    print(f"UI läuft auf http://127.0.0.1:{port}  (Strg+C zum Beenden)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
