"""
Scripted sample run for the UI demo mode — no API calls.

CRM results come from the real crm_core (so the numbers match the data); all
specialist/coordinator texts are invented placeholders and flagged as DEMO in the UI.
"""

import json

import crm_core

SPECIALISTS = [
    "Pricing Specialist",
    "Legal Reviewer",
    "Technical Fit Specialist",
    "Competitive Intel Analyst",
]


class _Call:  # mimics the fields crm_event() reads from an SDK event
    def __init__(self, name, input):
        self.name, self.input = name, input


def _crm(agent, name, args, fn):
    result = json.dumps(fn(**args), indent=2, ensure_ascii=False)
    return {"kind": "crm_call", "agent": agent, "name": name, "input": args, "result": result}


def script(coordinator: str):
    s = "Pricing Specialist"
    yield 0.4, {"kind": "session", "id": "demo-session"}
    yield 0.5, {"kind": "thread_status", "agent": coordinator, "state": "running"}
    yield 1.2, {"kind": "message", "agent": coordinator,
                "text": "Acme Corp, Enterprise-Datenplattform, 3 Jahre. Ich delegiere an alle vier Specialists parallel."}

    for name in SPECIALISTS:  # fan-out: all four within a second
        yield 0.15, {"kind": "thread_created", "agent": name}
        yield 0.1, {"kind": "delegate", "agent": name,
                    "text": f"{name}: Brief zur Acme-RFP, Antwort in einer Nachricht, ca. 300 Wörter."}
    for name in SPECIALISTS:
        yield 0.2, {"kind": "thread_status", "agent": name, "state": "running"}

    yield 2.0, {"kind": "tool", "agent": "Technical Fit Specialist", "name": "read"}
    yield 1.5, _crm(s, "search_past_deals", {"industry": "industrial", "outcome": "won"},
                    crm_core.search_past_deals)
    yield 1.0, {"kind": "reply", "agent": "Legal Reviewer",
                "text": "3 Flags: unbegrenzte Haftung (Blocker), IP-Übertragung (verhandelbar), MFN-Klausel (verhandelbar)."}
    yield 0.1, {"kind": "thread_status", "agent": "Legal Reviewer", "state": "idle"}
    yield 1.4, _crm(s, "get_deal", {"customer": "Initech"}, crm_core.get_deal)
    yield 1.6, {"kind": "reply", "agent": "Competitive Intel Analyst",
                "text": "Wahrscheinlich im Rennen: Microsoft Fabric und Snowflake. Positionierung über TCO und Echtzeit-Ingest."}
    yield 0.1, {"kind": "thread_status", "agent": "Competitive Intel Analyst", "state": "idle"}
    yield 1.1, {"kind": "reply", "agent": "Technical Fit Specialist",
                "text": "Fit: hoch. 14 von 16 Anforderungen voll erfüllt, 2 teilweise."}
    yield 0.1, {"kind": "thread_status", "agent": "Technical Fit Specialist", "state": "idle"}
    yield 1.5, {"kind": "reply", "agent": s,
                "text": "Empfehlung: 15 % Rabatt, 3 Jahre, jährlich im Voraus (Vergleich: Initech 15 %, Globex 18 %)."}
    yield 0.1, {"kind": "thread_status", "agent": s, "state": "idle"}

    yield 1.3, {"kind": "message", "agent": coordinator,
                "text": "**Executive Summary (DEMO)**\n- Hohe technische Passung, Preis bei 15 % Rabatt über 3 Jahre.\n"
                        "- Haftungsklausel ist ein Blocker, Gegenvorschlag: Cap auf 12 Monatsgebühren.\n"
                        "- Positionierung gegen Fabric über TCO und Echtzeit-Ingest."}
    yield 1.0, {"kind": "tool", "agent": coordinator, "name": "docx"}
    yield 1.5, {"kind": "thread_status", "agent": coordinator, "state": "idle"}
    yield 0.2, {"kind": "files", "files": []}
    yield 0.1, {"kind": "done", "session": "demo-session", "link": "", "demo": True}
