"""
Run the Deal Desk swarm against the synthetic RFP.

The RFP, product overview and the docx builder (tools/build_docx.py) are uploaded
via the Files API and mounted into the session's shared workspace
(/mnt/session/uploads/deal/), so the coordinator doesn't have to re-type them into files and
briefs (that cost ~90 s per run). Streams events as they come in so you can watch
the parallel thread fan-out — this is the demo, narrate it live.

The session setup (create_session, build_user_message) is shared with ui/app.py.

Saves the final transcript to outputs/.

Usage:
    python run_deal_desk.py
"""

import os
import sys
from pathlib import Path

from anthropic import Anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent / "crm-mcp"))
from custom_tool import is_crm_tool, run_crm_tool  # noqa: E402  (synthetic CRM)


ROOT = Path(__file__).resolve().parent
# The API mounts resources UNDER /mnt/session/uploads (mount_path "/deal/x" -> /mnt/session/uploads/deal/x).
# The workspace is shared: coordinator and specialists read the same paths.
FILES_DIR = "/mnt/session/uploads/deal"
WORK_DIR = "/workspace/deal/final"  # coordinator's scratch space for the proposal Markdown
SESSION_FILES = [
    # (local file, name inside FILES_DIR). past-wins.json is NOT here: the Pricing
    # Specialist queries it through the CRM tools.
    (ROOT / "synthetic-data" / "rfp-acme-corp.md", "rfp-acme-corp.md"),
    (ROOT / "synthetic-data" / "product-overview.md", "product-overview.md"),
    (ROOT / "tools" / "build_docx.py", "build_docx.py"),
]
OUTPUT_DIR = Path("outputs")

# House style. Sent inline with every run so the coordinator can't miss it
# (skill mounts aren't always visible inside the coordinator's container).
FIRM_VOICE_PATH = Path("skills/firm-voice/SKILL.md")


def load_firm_voice() -> str:
    if not FIRM_VOICE_PATH.exists():
        return ""
    print(f"  including firm voice: {FIRM_VOICE_PATH}")
    return (
        "\n\n=====  MANDATORY HOUSE STYLE (firm-voice)  =====\n"
        "This overrides any default branding. The proposal TEXT MUST use the "
        "company name, section headings, tone, humour, cover letter and sign-off "
        "defined below. build_docx.py applies the visual brand (colours, cover "
        "page, tables, callout boxes, footer) automatically, so ignore the hex "
        "values and never write python-docx code yourself. A proposal that says "
        "'BTS-Synthetic' as the company name is a FAILED deliverable.\n\n"
        + FIRM_VOICE_PATH.read_text(encoding="utf-8")
    )


def upload_session_files(client: Anthropic) -> list[dict]:
    """Upload the input files and return them as session `resources` (mounted under FILES_DIR)."""
    resources = []
    for path, name in SESSION_FILES:
        if not path.exists():
            raise SystemExit(f"Missing input file: {path}")
        # Originals expire after 24 h so repeated runs don't pile up files in the workspace.
        uploaded = client.beta.files.upload(
            file=(name, path.read_bytes(), "text/plain"), expires_in_seconds=86400
        )
        print(f"  uploaded {name} -> {FILES_DIR}/{name}")
        resources.append(
            {"type": "file", "file_id": uploaded.id, "mount_path": f"/deal/{name}"}
        )
    return resources


def create_session(client: Anthropic, title: str):
    """Create a session on the coordinator with the input files mounted. Shared with ui/app.py."""
    for needed in (".coordinator_id", ".environment_id"):
        if not (ROOT / needed).exists():
            raise SystemExit(
                f"Missing {needed}. Run setup_environment.py, create_specialists.py, "
                "upload_skills.py and create_coordinator.py first (or start.ps1)."
            )
    print("Uploading session files...")
    return client.beta.sessions.create(
        agent=(ROOT / ".coordinator_id").read_text().strip(),
        environment_id=(ROOT / ".environment_id").read_text().strip(),
        resources=upload_session_files(client),
        title=title,
    )


def build_user_message() -> str:
    """The kick-off message. Shared with ui/app.py so UI and CLI runs behave identically."""
    return (
        "An RFP has just landed. Run the standard Deal Desk process.\n\n"
        "FILES (already mounted in the shared workspace, NOT pasted here):\n"
        f"- {FILES_DIR}/rfp-acme-corp.md: the RFP\n"
        f"- {FILES_DIR}/product-overview.md: capability map (for Technical Fit)\n"
        f"- {FILES_DIR}/build_docx.py: Word builder (step 4)\n"
        "Never copy the content of these files into briefs, messages or other "
        "files: every specialist can read the same paths itself. Past-deal data "
        "is in the CRM tools (Pricing Specialist only).\n\n"
        "Process:\n"
        "1. Read the RFP file yourself. Don't explore the file system, "
        "everything you need is listed above.\n"
        "2. Delegate to all four specialists in ONE turn. Each brief: max 80 "
        "words, only the file path(s) to read and the one question to answer.\n"
        "3. Synthesise their replies.\n"
        "4. Write the proposal TEXT in the house style below as Markdown to "
        f"{WORK_DIR}/proposal.md (mkdir -p first), then build the Word "
        f"file: python3 {FILES_DIR}/build_docx.py {WORK_DIR}/proposal.md "
        "/mnt/session/outputs/Bogwater-Burr_Proposal_<Customer>.docx . "
        "Do NOT write your own python-docx code; the builder applies colours, "
        "cover page, tables, Swamp Wisdom boxes and footer. Markdown format: "
        "front matter between '---' lines (customer:, title:, date:), one "
        "'## Swamp Heading — Plain subtitle' per section, '> Swamp Wisdom: ...' "
        "callouts, '|' pipe tables, '- ' bullets, '1. ' numbered items. A blank "
        "line starts a new paragraph (consecutive lines are joined). If the "
        "builder reports an error, fix the Markdown and run it again.\n"
        "5. Before finishing, run the house-style self-check from the style "
        "guide and fix anything that fails. If a Critic is in your roster, ask "
        "it to check the voice, not just the substance.\n\n"
        "Specialists have their own skills attached for their respective "
        "domains. Move fast — the RFP deadline is real."
        f"{load_firm_voice()}"
    )


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    client = Anthropic()

    print("Starting session against the coordinator...")
    session = create_session(client, "Deal Desk — Acme Corp RFP")
    Path(".last_session_id").write_text(session.id)

    user_message = build_user_message()

    # Stream the events — this is the demo. Watch for parallel thread spawns.
    print("\n=== EVENT STREAM (this is the demo) ===\n")
    final_text_parts: list[str] = []

    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": user_message}],
                }
            ],
        )
        for event in stream:
            t = event.type
            if t == "session.thread_created":
                print(f"  [thread spawned]   {event.agent_name}", flush=True)
            elif t == "session.thread_status_running":
                name = getattr(event, "agent_name", "?")
                print(f"  [thread running]   {name}", flush=True)
            elif t == "agent.thread_message_received":
                print(f"  [reply ←]          {event.from_agent_name}", flush=True)
            elif t == "agent.thread_message_sent":
                print(f"  [delegate →]       {event.to_agent_name}", flush=True)
            elif t == "agent.message":
                for block in event.content:
                    if getattr(block, "type", None) == "text":
                        final_text_parts.append(block.text)
                        print(block.text, end="", flush=True)
            elif t == "agent.custom_tool_use":
                if is_crm_tool(event.name):
                    print(f"\n  [CRM ← {event.name}] {event.input}", flush=True)
                    result_event = {
                        "type": "user.custom_tool_result",
                        "custom_tool_use_id": event.id,
                        "content": [
                            {"type": "text", "text": run_crm_tool(event.name, event.input)}
                        ],
                    }
                    if event.session_thread_id:  # call came from a subagent thread
                        result_event["session_thread_id"] = event.session_thread_id
                    client.beta.sessions.events.send(session.id, events=[result_event])
            elif t == "agent.tool_use":
                print(f"\n  [tool: {getattr(event, 'name', '?')}]", flush=True)
            elif t == "session.status_idle":
                # Custom tool calls pause the session; keep streaming until we answered.
                if event.stop_reason.type == "requires_action":
                    continue
                print("\n\n[swarm finished]")
                break

    OUTPUT_DIR.mkdir(exist_ok=True)
    transcript_path = OUTPUT_DIR / "coordinator-transcript.txt"
    transcript_path.write_text("".join(final_text_parts), encoding="utf-8")
    print(f"\nCoordinator transcript saved to {transcript_path}")

    # Pull every file the agents produced in the container
    print("\nDownloading deliverables from the session container...")
    files = client.beta.files.list(
        scope_id=session.id,
        betas=["managed-agents-2026-04-01"],
    )
    file_count = 0
    for f in files.data:
        out_path = OUTPUT_DIR / f.filename
        print(f"  {f.filename}  ->  {out_path}")
        content = client.beta.files.download(f.id)
        content.write_to_file(str(out_path))
        file_count += 1

    if file_count == 0:
        print("  (no files found — agents may have produced text-only output)")
    else:
        print(f"\nDownloaded {file_count} file(s) to {OUTPUT_DIR}/")

    print(f"\nView the full session (including all sub-agent threads) at:")
    print(f"  https://platform.claude.com/sessions/{session.id}")


if __name__ == "__main__":
    main()
