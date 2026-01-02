import os
import sys
import base64
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from duckduckgo_search import DDGS

# ------------------ CONFIG ------------------
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "castsoftware")
ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # optional

# ------------------ APP ------------------
app = FastAPI(title="MCP Web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ AUTH ------------------
def zendesk_headers():
    if not ZENDESK_EMAIL or not ZENDESK_API_TOKEN:
        raise HTTPException(500, "Zendesk credentials not configured")

    auth = f"{ZENDESK_EMAIL}/token:{ZENDESK_API_TOKEN}"
    encoded = base64.b64encode(auth.encode()).decode()

    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "User-Agent": "MCP-Web",
    }

# ------------------ MODELS ------------------
class TicketRequest(BaseModel):
    ticket_id: int

class QueryRequest(BaseModel):
    query: str

# ------------------ UTIL ------------------
def summarize_comments(comments):
    if not comments:
        return "No discussion available."

    resolution_lines = []
    for c in comments:
        body = c.get("plain_body", "").lower()
        if any(k in body for k in ["resolved", "fixed", "solution", "upgrade", "workaround"]):
            resolution_lines.append(c.get("plain_body"))

    if resolution_lines:
        return resolution_lines[-1][:1000]

    return comments[-1].get("plain_body", "")[:1000]

def fallback_summary(ticket, comments, related_tickets, docs):
    return f"""
Issue Summary:
{ticket['subject']}

Observed Behavior:
{len(comments)} comments discussing the issue.

Similar Issues:
{len(related_tickets)} related tickets found.

Documentation Insights:
{len(docs)} relevant docs found.

Suggested Resolution:
Upgrade, workaround, or configuration change recommended based on history.

Confidence Score: {round(min(0.3 + len(comments)/100, 0.9), 2)}
""".strip()

# ------------------ ROUTES ------------------
@app.get("/debug/zendesk")
def debug_zendesk():
    return {
        "email_loaded": bool(ZENDESK_EMAIL),
        "token_loaded": bool(ZENDESK_API_TOKEN),
        "subdomain": ZENDESK_SUBDOMAIN,
    }

# ------------------ TICKET DETAILS ------------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    headers = zendesk_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        ticket_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}.json",
            headers=headers,
        )
        ticket_resp.raise_for_status()
        ticket = ticket_resp.json()["ticket"]

        comments_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}/comments.json",
            headers=headers,
        )
        comments = comments_resp.json().get("comments", [])

        summary_text = summarize_comments(comments)

        # Related tickets search
        search_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
            headers=headers,
            params={"query": ticket["subject"]},
        )
        related = search_resp.json().get("results", [])[:3]

        # Docs
        docs = []
        with DDGS() as ddgs:
            docs = list(ddgs.text(f"site:doc.castsoftware.com {ticket['subject']}", max_results=3))

        summary = fallback_summary(ticket, comments, related, docs)

        html = f"""
<h2>TICKET #{ticket['id']}</h2>
<b>Status:</b> {ticket['status']}<br><br>

<h3>Description</h3>
<pre>{ticket['description']}</pre>

<h3>Summary & Suggested Resolution</h3>
<pre>{summary}</pre>

<h3>Related Tickets</h3>
<ul>
{''.join(f"<li>#{t['id']} - {t['subject']}</li>" for t in related)}
</ul>
"""

        return {
            "summary": summary,
            "confidence": round(min(0.3 + len(comments)/100, 0.9), 2),
            "related_tickets": related,
            "related_docs": docs,
            "html": html,
        }

# ------------------ SEARCH ALL ------------------
@app.post("/search/all")
async def search_all(req: QueryRequest):
    headers = zendesk_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        ticket_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
            headers=headers,
            params={"query": req.query},
        )
        tickets = ticket_resp.json().get("results", [])

    docs = []
    with DDGS() as ddgs:
        docs = list(ddgs.text(f"site:doc.castsoftware.com {req.query}", max_results=5))

    summary = (
        f"Search '{req.query}' found {len(tickets)} tickets "
        f"and {len(docs)} documentation references. "
        "Review similar tickets and docs for resolution."
    )

    return {
        "query": req.query,
        "summary": summary,
        "tickets": tickets[:5],
        "docs": docs,
    }

# ------------------ HEALTH ------------------
@app.get("/")
def health():
    return {"status": "ok"}
