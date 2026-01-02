import os
import re
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from collections import Counter
from typing import List, Optional

# ------------------ APP SETUP ------------------
app = FastAPI(title="Zendesk Smart Search API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ ENV ------------------
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN")
ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN")
API_KEY = os.getenv("API_KEY")  # optional (DEV-friendly)

ZENDESK_BASE = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2"

# ------------------ MODELS ------------------
class TicketRequest(BaseModel):
    ticket_id: int


class SearchRequest(BaseModel):
    query: str


# ------------------ AUTH ------------------
def check_api_key(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def zendesk_headers():
    return {
        "Authorization": (
            "Basic "
            + httpx._models._base64.b64encode(
                f"{ZENDESK_EMAIL}/token:{ZENDESK_API_TOKEN}".encode()
            ).decode()
        ),
        "Content-Type": "application/json",
    }


# ------------------ HELPERS ------------------
RESOLUTION_KEYWORDS = {
    "upgrade": [
        "upgrade",
        "8.4",
        "new version",
        "latest version",
        "move to",
    ],
    "workaround": [
        "workaround",
        "exception",
        "exclude",
        "temporary",
        "mitigate",
    ],
    "not_supported": [
        "not supported",
        "cannot fix",
        "limitation",
        "no fix",
    ],
}


RESOLUTION_TEXT = {
    "upgrade": "Upgrade to a newer supported product/runtime version where this limitation is resolved.",
    "workaround": "Apply a documented workaround or exception to mitigate the issue.",
    "not_supported": "This is a known limitation and cannot be fixed in the current version.",
    "unknown": "No definitive resolution identified. Further investigation may be required.",
}


def extract_resolution_signals(text: str, counter: Counter):
    t = text.lower()
    for key, words in RESOLUTION_KEYWORDS.items():
        if any(w in t for w in words):
            counter[key] += 1


def infer_resolution(comments: List[str]):
    counter = Counter()
    for c in comments:
        extract_resolution_signals(c, counter)

    if not counter:
        return "unknown", 0.1

    dominant, count = counter.most_common(1)[0]
    confidence = min(0.95, count / max(1, sum(counter.values())))
    return dominant, round(confidence, 2)


def summarize_comments(comments: List[str]) -> str:
    if not comments:
        return "No significant discussion found."

    return (
        f"Reviewed {len(comments)} comments. "
        f"Discussion focuses on root cause analysis, version compatibility, "
        f"and possible upgrade or workaround paths."
    )


# ------------------ ZENDESK API ------------------
async def get_ticket(ticket_id: int):
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ZENDESK_BASE}/tickets/{ticket_id}.json",
            headers=zendesk_headers(),
        )
        r.raise_for_status()
        return r.json()["ticket"]


async def get_ticket_comments(ticket_id: int):
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ZENDESK_BASE}/tickets/{ticket_id}/comments.json",
            headers=zendesk_headers(),
        )
        r.raise_for_status()
        return [
            c["body"] for c in r.json()["comments"] if not c.get("public") is False
        ]


async def search_tickets(query: str):
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ZENDESK_BASE}/search.json",
            params={"query": query, "type": "ticket"},
            headers=zendesk_headers(),
        )
        r.raise_for_status()
        return r.json()["results"]


# ------------------ SHARED SUMMARY ENGINE ------------------
async def build_summary_from_tickets(tickets: List[dict]):
    all_comments = []
    related = []

    for t in tickets[:10]:  # limit for performance
        try:
            comments = await get_ticket_comments(t["id"])
            all_comments.extend(comments)
            related.append(
                {
                    "id": t["id"],
                    "subject": t["subject"],
                    "status": t["status"],
                }
            )
        except Exception:
            continue

    resolution_key, confidence = infer_resolution(all_comments)

    summary = {
        "problem_summary": (
            f"Multiple tickets describe a recurring issue related to the searched topic."
        ),
        "comment_summary": summarize_comments(all_comments),
        "suggested_resolution": RESOLUTION_TEXT[resolution_key],
        "confidence": confidence,
        "related_tickets": related[:3],
    }

    return summary


# ------------------ ROUTES ------------------
@app.post("/ticket/details")
async def ticket_details(
    req: TicketRequest, x_api_key: Optional[str] = Header(None)
):
    check_api_key(x_api_key)

    ticket = await get_ticket(req.ticket_id)
    comments = await get_ticket_comments(req.ticket_id)

    resolution_key, confidence = infer_resolution(comments)

    summary = {
        "summary": {
            "issue": ticket["subject"],
            "observed_behavior": summarize_comments(comments),
            "suggested_resolution": RESOLUTION_TEXT[resolution_key],
            "confidence": confidence,
        }
    }

    return summary


@app.post("/search/all")
async def search_all(
    req: SearchRequest, x_api_key: Optional[str] = Header(None)
):
    check_api_key(x_api_key)

    tickets = await search_tickets(req.query)

    if not tickets:
        return {
            "query": req.query,
            "summary": "No related tickets found.",
        }

    summary = await build_summary_from_tickets(tickets)

    return {
        "query": req.query,
        "summary": summary,
        "ticket_count": len(tickets),
    }


@app.get("/health")
def health():
    return {"status": "ok"}
