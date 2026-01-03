import os
import re
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

# ------------------ APP SETUP ------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

ZENDESK_COOKIE = os.getenv("ZENDESK_COOKIE")

# ------------------ MODELS ------------------
class TicketRequest(BaseModel):
    ticket_id: int


# ------------------ TEXT CLEANING ------------------
def clean_comment(text: str) -> str:
    if not text:
        return ""

    # Remove CID images
    text = re.sub(r'\[cid:.*?\]', '', text)

    # Remove URLs
    text = re.sub(r'http\S+', '', text)

    # Mask long secrets (API keys, tokens)
    text = re.sub(r'[A-Za-z0-9]{20,}', '[REDACTED]', text)

    # Remove signatures
    text = re.split(r'\n--\s|\nRegards,|\nThanks,', text, flags=re.I)[0]

    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text


# ------------------ COMMENT CLASSIFICATION ------------------
SOLUTION_KEYWORDS = [
    "run with admin",
    "run as administrator",
    "please run",
    "resolved by",
    "solution is",
    "fix is",
    "workaround",
    "recommended",
    "try running"
]

def split_issue_solution(comments: list[str]):
    issue = []
    solution = []

    for c in comments:
        lc = c.lower()
        if any(k in lc for k in SOLUTION_KEYWORDS):
            solution.append(c)
        else:
            issue.append(c)

    return " ".join(issue), " ".join(solution)


# ------------------ ZENDESK FETCH ------------------
async def fetch_ticket(ticket_id: int):
    url = f"https://castsoftware.zendesk.com/api/v2/tickets/{ticket_id}/comments.json"
    headers = {"Cookie": ZENDESK_COOKIE}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()["comments"]


# ------------------ ANALYSIS LOGIC ------------------
def analyze_ticket(comments: list[str]):
    cleaned = [clean_comment(c) for c in comments if c.strip()]

    issue_text, solution_text = split_issue_solution(cleaned)

    # If solution exists → trust ticket, not AI
    if solution_text:
        return {
            "summary": f"Observed Behavior:\n{issue_text}\n\nResolution:\n{solution_text}",
            "confidence": 0.75,
            "recommended_solution": solution_text
        }

    # Otherwise → OpenAI fallback
    prompt = f"""
You are analyzing a CAST support ticket.

ISSUE:
{issue_text}

Provide:
1. Clear issue summary (2 lines)
2. Recommended resolution
3. Confidence score between 0 and 1
"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    return {
        "summary": resp.choices[0].message.content,
        "confidence": 0.5,
        "recommended_solution": "Review related tickets or collect more logs."
    }


# ------------------ API ENDPOINT ------------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    comments_raw = await fetch_ticket(req.ticket_id)
    comments_text = [c.get("body", "") for c in comments_raw]

    analysis = analyze_ticket(comments_text)

    return {
        "summary": analysis["summary"],
        "confidence": analysis["confidence"],
        "related_tickets": [
            {
                "id": req.ticket_id,
                "url": f"https://castsoftware.zendesk.com/agent/tickets/{req.ticket_id}"
            }
        ],
        "related_docs": {},
        "recommended_solution": analysis["recommended_solution"]
    }
