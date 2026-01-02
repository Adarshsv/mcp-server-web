import os
import re
import base64
import httpx
from typing import List
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------- CONFIG ----------------
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN")
ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN")
OPTIONAL_API_KEY = os.getenv("API_KEY")  # optional

ZENDESK_BASE = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2"

auth = base64.b64encode(
    f"{ZENDESK_EMAIL}/token:{ZENDESK_API_TOKEN}".encode()
).decode()

ZENDESK_HEADERS = {
    "Authorization": f"Basic {auth}",
    "Content-Type": "application/json"
}

# ---------------- APP ----------------
app = FastAPI(title="Zendesk Intelligence API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["*"],
    allow_methods=["*"],
)

# ---------------- MODELS ----------------
class TicketRequest(BaseModel):
    ticket_id: int

class SearchRequest(BaseModel):
    query: str

# ---------------- HELPERS ----------------
async def zendesk_get(client, path):
    r = await client.get(f"{ZENDESK_BASE}{path}", headers=ZENDESK_HEADERS)
    r.raise_for_status()
    return r.json()

def extract_keywords(text: str) -> List[str]:
    words = re.findall(r"[A-Za-z0-9\.\+\-]{4,}", text)
    return list(set(words))[:12]

def build_fallback_summary(ticket, comments, related_tickets, related_docs):
    return f"""
Issue Summary:
{ticket['subject']}

Observed Behavior:
Found {len(comments)} comments describing the problem.

Similar Issues:
{len(related_tickets)} related tickets found.

Documentation Insights:
{len(related_docs)} relevant docs found.

Suggested Resolution:
Review previous tickets and documentation. Similar cases often require upgrade,
configuration change, or product limitation acknowledgment.

Confidence Score: {min(0.9, 0.1 + len(related_tickets) * 0.2)}
""".strip()

# ---------------- ROUTES ----------------
@app.post("/ticket/details")
async def ticket_details(
    req: TicketRequest,
    x_api_key: str | None = Header(default=None)
):
    if OPTIONAL_API_KEY and x_api_key and x_api_key != OPTIONAL_API_KEY:
        raise HTTPException(401, "Invalid API key")

    async with httpx.AsyncClient(timeout=30) as client:
        ticket = (await zendesk_get(client, f"/tickets/{req.ticket_id}.json"))["ticket"]
        comments = (await zendesk_get(
            client, f"/tickets/{req.ticket_id}/comments.json"
        ))["comments"]

        full_text = ticket["subject"] + " " + ticket.get("description", "")
        for c in comments:
            full_text += " " + c.get("body", "")

        keywords = extract_keywords(full_text)
        query = " ".join(keywords)

        search = await zendesk_get(
            client,
            f"/search.json?query=type:ticket {query}"
        )

        related_tickets = [
            {
                "id": t["id"],
                "subject": t["subject"],
                "status": t["status"]
            }
            for t in search["results"]
            if t["id"] != req.ticket_id
        ][:5]

        # Docs (best effort – Zendesk Guide)
        docs = []
        try:
            doc_search = await zendesk_get(
                client,
                f"/help_center/articles/search.json?query={query}"
            )
            docs = doc_search.get("results", [])[:5]
        except:
            pass

        summary = build_fallback_summary(
            ticket, comments, related_tickets, docs
        )

        confidence = min(0.9, 0.1 + len(related_tickets) * 0.2)

        # -------- HTML --------
        html = f"<h2>TICKET #{ticket['id']}</h2>"
        html += f"<b>Status:</b> {ticket['status']}<br><br>"
        html += f"<h3>Description</h3><pre>{ticket.get('description','')}</pre>"
        html += f"<h3>Summary & Suggested Resolution</h3><pre>{summary}</pre>"
        html += "<h3>History</h3>"

        for c in comments:
            html += f"<p><b>User {c['author_id']}:</b><br>{c['body']}</p>"

        return {
            "summary": summary,
            "confidence": confidence,
            "related_tickets": related_tickets,
            "related_docs": docs,
            "html": html
        }

@app.post("/search/all")
async def search_all(
    req: SearchRequest,
    x_api_key: str | None = Header(default=None)
):
    if OPTIONAL_API_KEY and x_api_key and x_api_key != OPTIONAL_API_KEY:
        raise HTTPException(401, "Invalid API key")

    async with httpx.AsyncClient(timeout=30) as client:
        ticket_search = await zendesk_get(
            client,
            f"/search.json?query=type:ticket {req.query}"
        )

        doc_search = []
        try:
            docs = await zendesk_get(
                client,
                f"/help_center/articles/search.json?query={req.query}"
            )
            doc_search = docs.get("results", [])
        except:
            pass

        summary = f"""
Search Keyword: "{req.query}"

Tickets Found: {len(ticket_search['results'])}
Documents Found: {len(doc_search)}

Suggested Action:
Review similar tickets and documentation to identify recurring solutions.
""".strip()

        return {
            "query": req.query,
            "summary": summary,
            "tickets": {
                "count": len(ticket_search["results"]),
                "results": ticket_search["results"][:10]
            },
            "docs": {
                "count": len(doc_search),
                "results": doc_search[:10]
            }
        }
