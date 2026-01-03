import os
import re
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ------------------ APP SETUP ------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ZENDESK_COOKIE = os.getenv("ZENDESK_COOKIE")

# ------------------ MODELS ------------------
class TicketRequest(BaseModel):
    ticket_id: int


# ------------------ CLEANING ------------------
def clean(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r'\[cid:.*?\]', '', text)
    text = re.sub(r'http\S+', '', text)
    text = re.sub(r'[A-Za-z0-9]{25,}', '[REDACTED]', text)
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


# ------------------ EXTRACTION ------------------
def extract_resolution(text: str) -> str | None:
    patterns = [
        r'please run .*?admin',
        r'run .*?administrator',
        r'resolved by .*',
        r'workaround .*',
        r'solution .*',
    ]

    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0).capitalize()

    return None


# ------------------ ZENDESK ------------------
async def fetch_comments(ticket_id: int):
    url = f"https://castsoftware.zendesk.com/api/v2/tickets/{ticket_id}/comments.json"
    headers = {"Cookie": ZENDESK_COOKIE}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()["comments"]


# ------------------ ANALYSIS ------------------
def analyze(comments):
    user_issue = []
    agent_replies = []

    for c in comments:
        body = clean(c.get("body", ""))
        if not body:
            continue

        author = c.get("author_id", "")
        role = c.get("via", {}).get("source", {}).get("from", {}).get("name", "")

        # Heuristic: agent replies usually shorter and directive
        if "please" in body.lower() or "run" in body.lower():
            agent_replies.append(body)
        else:
            user_issue.append(body)

    issue_text = " ".join(user_issue[:2])

    # 🔥 Look for solution in LAST agent reply
    for reply in reversed(agent_replies):
        resolution = extract_resolution(reply)
        if resolution:
            return {
                "summary": f"Observed Behavior:\n{issue_text}",
                "confidence": 0.85,
                "recommended_solution": resolution
            }

    # fallback (no solution found)
    return {
        "summary": f"Observed Behavior:\n{issue_text}",
        "confidence": 0.5,
        "recommended_solution": "Collect logs and escalate for investigation."
    }


# ------------------ API ------------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    comments = await fetch_comments(req.ticket_id)import os
import re
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ------------------ APP SETUP ------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ZENDESK_COOKIE = os.getenv("ZENDESK_COOKIE")

# ------------------ MODELS ------------------
class TicketRequest(BaseModel):
    ticket_id: int


# ------------------ CLEANING ------------------
def clean(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r'\[cid:.*?\]', '', text)
    text = re.sub(r'http\S+', '', text)
    text = re.sub(r'[A-Za-z0-9]{25,}', '[REDACTED]', text)
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


# ------------------ EXTRACTION ------------------
def extract_resolution(text: str) -> str | None:
    patterns = [
        r'please run .*?admin',
        r'run .*?administrator',
        r'resolved by .*',
        r'workaround .*',
        r'solution .*',
    ]

    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0).capitalize()

    return None


# ------------------ ZENDESK ------------------
async def fetch_comments(ticket_id: int):
    url = f"https://castsoftware.zendesk.com/api/v2/tickets/{ticket_id}/comments.json"
    headers = {"Cookie": ZENDESK_COOKIE}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()["comments"]


# ------------------ ANALYSIS ------------------
def analyze(comments):
    user_issue = []
    agent_replies = []

    for c in comments:
        body = clean(c.get("body", ""))
        if not body:
            continue

        author = c.get("author_id", "")
        role = c.get("via", {}).get("source", {}).get("from", {}).get("name", "")

        # Heuristic: agent replies usually shorter and directive
        if "please" in body.lower() or "run" in body.lower():
            agent_replies.append(body)
        else:
            user_issue.append(body)

    issue_text = " ".join(user_issue[:2])

    # 🔥 Look for solution in LAST agent reply
    for reply in reversed(agent_replies):
        resolution = extract_resolution(reply)
        if resolution:
            return {
                "summary": f"Observed Behavior:\n{issue_text}",
                "confidence": 0.85,
                "recommended_solution": resolution
            }

    # fallback (no solution found)
    return {
        "summary": f"Observed Behavior:\n{issue_text}",
        "confidence": 0.5,
        "recommended_solution": "Collect logs and escalate for investigation."
    }


# ------------------ API ------------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    comments = await fetch_comments(req.ticket_id)
    result = analyze(comments)

    return {
        "summary": result["summary"],
        "confidence": result["confidence"],
        "related_tickets": [
            {
                "id": req.ticket_id,
                "url": f"https://castsoftware.zendesk.com/agent/tickets/{req.ticket_id}"
            }
        ],
        "related_docs": {},
        "recommended_solution": result["recommended_solution"]
    }
