import os
import sys
import base64
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from duckduckgo_search import DDGS

# ------------------ ENV ------------------
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "castsoftware")

# ------------------ APP ------------------
app = FastAPI(title="MCP Web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ STARTUP ------------------
@app.on_event("startup")
async def startup():
    print("[STARTUP] Zendesk Email:", bool(os.getenv("ZENDESK_EMAIL")), file=sys.stderr)
    print("[STARTUP] Zendesk Token:", bool(os.getenv("ZENDESK_API_TOKEN")), file=sys.stderr)

# ------------------ AUTH ------------------
def zendesk_headers():
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")
    if not email or not token:
        raise HTTPException(500, "Zendesk credentials missing")

    auth = base64.b64encode(f"{email}/token:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
        "User-Agent": "MCP-Web",
    }

# ------------------ MODELS ------------------
class TicketRequest(BaseModel):
    ticket_id: int

class QueryRequest(BaseModel):
    query: str

# ------------------ HELPERS ------------------
def extract_keywords(text: str, limit=20):
    words = [w for w in text.replace("\n", " ").split() if len(w) > 4]
    return " ".join(words[:limit])

def search_docs(query: str):
    docs = []
    with DDGS() as ddgs:
        for r in ddgs.text(f"site:doc.castsoftware.com {query}", max_results=5):
            docs.append({
                "title": r["title"],
                "url": r["href"],
                "snippet": r["body"],
            })
    return docs

async def search_tickets(query: str):
    headers = zendesk_headers()
    url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json"

    async with httpx.AsyncClient() as client:
        r = await client.get(url, params={"query": query}, headers=headers)

    results = []
    for t in r.json().get("results", []):
        results.append({
            "id": t["id"],
            "subject": t["subject"],
            "status": t["status"],
        })
    return results

# ------------------ TICKET DETAILS ------------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    headers = zendesk_headers()

    async with httpx.AsyncClient() as client:
        t = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}.json",
            headers=headers,
        )
        c = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}/comments.json",
            headers=headers,
        )

    ticket = t.json()["ticket"]
    comments = c.json().get("comments", [])

    # ---------- TEXT AGGREGATION ----------
    history_text = " ".join(c.get("plain_body", "") for c in comments)
    combined_text = f"{ticket['subject']} {ticket.get('description','')} {history_text}"

    keywords = extract_keywords(combined_text)

    # ---------- CROSS SEARCH ----------
    related_tickets = await search_tickets(keywords)
    related_docs = search_docs(keywords)

    # ---------- STRUCTURED SUMMARY ----------
    summary = f"""
Problem:
{ticket['subject']}

Observed Behavior:
This issue was discussed across {len(comments)} ticket comments. The problem persists or required clarification across multiple interactions.

Similar Issues:
{len(related_tickets)} related tickets were found. This suggests the issue may be recurring or previously investigated.

Documentation Insight:
{len(related_docs)} relevant documentation references were identified.

Suggested Resolution:
"""

    if related_tickets:
        summary += (
            "- Review similar resolved tickets for confirmed workarounds or fixes.\n"
            "- Validate whether the resolution applies to the current product version.\n"
        )
    elif related_docs:
        summary += (
            "- Follow configuration or compatibility guidance from CAST documentation.\n"
            "- Verify product version alignment.\n"
        )
    else:
        summary += (
            "- No direct match found.\n"
            "- Collect logs, reproduction steps, and escalate to R&D.\n"
        )

    # ---------- HTML ----------
    history_html = "".join(
        f"<p><b>User {c['author_id']}:</b><br>{c.get('plain_body','')}</p>"
        for c in comments
    )

    html = f"""
    <h2>TICKET #{ticket['id']}</h2>
    <b>Status:</b> {ticket['status']}<br><br>

    <h3>Description</h3>
    <pre>{ticket.get('description','')}</pre>

    <h3>Summary & Suggested Resolution</h3>
    <pre>{summary}</pre>

    <h3>History</h3>
    {history_html}
    """

    return {
        "summary": summary.strip(),
        "related_tickets": related_tickets,
        "related_docs": related_docs,
        "html": html,
    }

# ------------------ SEARCH ALL ------------------
@app.post("/search/all")
async def search_all(req: QueryRequest):
    tickets = await search_tickets(req.query)
    docs = search_docs(req.query)

    summary = (
        f"Searched tickets (including comments) and documentation.\n"
        f"Tickets found: {len(tickets)}\n"
        f"Docs found: {len(docs)}\n"
    )

    return {
        "query": req.query,
        "summary": summary,
        "tickets": tickets,
        "docs": docs,
    }

# ------------------ HEALTH ------------------
@app.get("/")
def health():
    return {"status": "ok"}
