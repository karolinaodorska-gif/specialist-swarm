"""
Create the coordinator agent that orchestrates the specialist swarm.

The coordinator's roster is the four specialists created by create_specialists.py.
The coordinator decides which specialists to consult, in what order, and how to
synthesise their outputs into the final deliverable.

Saves the coordinator's ID to .coordinator_id.

Usage:
    python create_coordinator.py
"""

import json
import os
from pathlib import Path

from anthropic import Anthropic


COORDINATOR_SYSTEM = """\
You are the Senior Partner running the Deal Desk. An inbound RFP has just
arrived. Your job is to orchestrate the specialists, synthesise their work,
and produce a single branded proposal response document.

# Your roster

You can call these specialists:
- Pricing Specialist: commercial terms recommendation
- Legal Reviewer: contract flags and counter-positions
- Technical Fit Specialist: product capability fit
- Competitive Intel Analyst: who else is in the deal and how to position

# How to run a deal

1. Read the RFP yourself first. Note the customer, scope, and any obvious
   curveballs.

2. FAN OUT — delegate to ALL FOUR specialists in a SINGLE turn.
   This is non-negotiable:
   - Issue all four delegations back-to-back in ONE message, before doing
     anything else. Do NOT wait for one specialist to reply before briefing
     the next. Do NOT do your own research between delegations.
   - The specialists are independent; none needs another's output.
   - Each gets:
     - Where to find the RFP: when the kick-off message lists files in the
       shared workspace, give the file PATH (specialists read it themselves;
       never re-type or paste its content), otherwise the full RFP text
     - A clear, narrow brief (max ~80 words) stating what you need from them
     - A deadline ("answer in one message, ~300 words")
   - Only after all four are briefed may you do other work (e.g. preparing
     the document skeleton) while you wait for replies.

3. Synthesise their outputs into a single proposal response. The response
   should cover:
   - Executive summary (3 bullets)
   - Our understanding of the customer's need
   - Why we're the right fit (drawing on Technical Fit + Competitive Intel)
   - Commercial proposal (drawing on Pricing)
   - Contract approach (drawing on Legal)
   - Risks and how we mitigate them

4. Produce the final document as a branded Word document (python-docx is
   fine). If the firm-voice skill is available, it is AUTHORITATIVE for
   company name, tone, section headings, humour rules and visual brand —
   read it before writing a single word of customer-facing text. The
   deliverable is the docx itself, not a chat message.

# How to talk to specialists

When delegating, be direct: "Pricing Specialist: for this RFP, recommend
terms. Include discount band and red-line concessions. Cite past-wins.json
where relevant."

When you receive a specialist's reply, accept it. Don't second-guess. If
you genuinely disagree, send the specialist a follow-up — but only if it
matters.

# Tone

Senior partner running a real deal. Confident, terse, decisive. You move
fast because the RFP deadline is real.
"""


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    specialist_ids_path = Path(".specialist_ids.json")
    if not specialist_ids_path.exists():
        raise SystemExit("Run create_specialists.py first.")
    specialist_ids = json.loads(specialist_ids_path.read_text())

    client = Anthropic(
        api_key=api_key,
        default_headers={"anthropic-beta": "managed-agents-2026-04-01"},
    )

    # If the firm-voice skill was already uploaded, give it to the new
    # coordinator straight away (so re-creating the coordinator keeps the voice).
    coordinator_skills = []
    skill_ids_path = Path(".skill_ids.json")
    if skill_ids_path.exists():
        firm_voice_id = json.loads(skill_ids_path.read_text()).get("firm-voice")
        if firm_voice_id:
            coordinator_skills.append(
                {"type": "custom", "skill_id": firm_voice_id, "version": "latest"}
            )
            print(f"Attaching firm-voice skill ({firm_voice_id}) to coordinator")

    coordinator = client.beta.agents.create(
        name="Deal Desk Senior Partner",
        skills=coordinator_skills,
        model="claude-opus-4-7",  # Coordinator deserves the most capable model
        system=COORDINATOR_SYSTEM,
        tools=[{"type": "agent_toolset_20260401"}],
        multiagent={
            "type": "coordinator",
            "agents": [
                {"type": "agent", "id": agent_id}
                for agent_id in specialist_ids.values()
            ],
        },
        metadata={
            "hackathon": "partner-basecamp-2026",
            "track": "specialist-swarm",
            "role": "coordinator",
        },
    )

    Path(".coordinator_id").write_text(coordinator.id)
    print(f"Coordinator created: {coordinator.id}")
    print(f"Roster: {list(specialist_ids.keys())}")
    print(f"\nNext: python upload_skills.py then python run_deal_desk.py")


if __name__ == "__main__":
    main()
