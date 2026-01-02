import os
import sys
import base64
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from duckduckgo_search import DDGS

# ------------------ ENV SETUP ------------------
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "castsoftware")

# ----------------- APP -----------------
app = FastAPI(title="MCP Web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- STARTUP LOG ----------------
@app.on_event("startup")
async def startup():
    print("[STARTUP] Zendesk Email Loaded:", bool(os.getenv("ZENDESK_EMAIL")), file=sys.stderr)
    print("[STARTUP] Zendesk API Token Loaded:", bool(os.getenv("ZENDESK_API_TOKEN")), file=sys.stderr)
    print("[STARTUP] Zendesk Subdomain:", ZENDESK_SUBDOMAIN, file=sys.stderr)

# ---------------- AUTH ----------------
def zendesk_headers():
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")

    if not email or not token:
        raise HTTPException(status_code=500, detail="Zendesk credentials not set")

    auth = base64.b64encode(f"{email}/token:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
        "User-Agent": "MCP-Web",
    }

# ---------------- MODELS ----------------
class QueryRequest(BaseModel):
    query: str

class TicketRequest(BaseModel):
    ticket_id: int

# ---------------- DEBUG ----------------
@app.get("/debug/zendesk")
def debug_zendesk():
    return {
        "email": os.getenv("ZENDESK_EMAIL"),
        "token": os.getenv("ZENDESK_API_TOKEN"),
        "subdomain": ZENDESK_SUBDOMAIN,
    }

# ---------------- DOC SEARCH ----------------
def search_docs_internal(query: str):
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(f"site:doc.castsoftware.com {query}", max_results=5):
            results.append({
                "title": r["title"],
                "url": r["href"],
                "snippet": r["body"]
            })
    return results

# ---------------- TICKET SEARCH ----------------
async def search_tickets_internal(query: str):
    headers = zendesk_headers()
    url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json"

    async with httpx.AsyncClient() as client:
        r = await client.get(url, params={"query": query}, headers=headers)

    tickets = []
    for t in r.json().get("results", []):
        tickets.append({
            "id": t["id"],
            "subject": t["subject"],
            "status": t["status"]
        })

    return tickets

# ---------------- ALL SEARCH ----------------
@app.post("/search/all")
async def search_all(req: QueryRequest):
    tickets = await search_tickets_internal(req.query)
    docs = search_docs_internal(req.query)

    summary = (
        f"Searched across Zendesk tickets (including comments) and CAST documentation.\n"
        f"Found {len(tickets)} related tickets and {len(docs)} documentation references."
    )

    return {
        "query": req.query,
        "summary": summary,
        "tickets": {"count": len(tickets), "results": tickets},
        "docs": {"count": len(docs), "results": docs},
    }

# ---------------- TICKET DETAILS (SMART) ----------------
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

    # ---------- BUILD SEARCH TEXT ----------
    comment_text = " ".join(c.get("plain_body", "") for c in comments)
    search_text = f"{ticket['subject']} {ticket.get('description','')} {comment_text}"
    keywords = " ".join(search_text.split()[:12])

    # ---------- CROSS SEARCH ----------
    related_tickets = await search_tickets_internal(keywords)
    related_docs = search_docs_internal(keywords)

    # ---------- SUMMARY (ALWAYS) ----------
    summary = (
        f"Issue Summary:\n"
        f"{ticket['subject']}\n\n"
        f"Analysis:\n"
        f"- Ticket history reviewed ({len(comments)} comments)\n"
        f"- Related tickets found: {len(related_tickets)}\n"
        f"- Relevant documentation found: {len(related_docs)}\n\n"
    )

    if related_tickets:
        summary += (
            "Suggested Solution:\n"
            "This issue appears to be previously reported. "
            "Review similar resolved tickets for an existing workaround or fix.\n"
        )
    elif related_docs:
        summary += (
            "Suggested Solution:\n"
            "Documentation suggests this is a known configuration or compatibility issue. "
            "Validate against the referenced CAST documentation.\n"
        )
    else:
        summary += (
            "Suggested Solution:\n"
            "No direct matches found. Recommend escalation to R&D with logs and reproduction steps.\n"
        )

    # ---------- HTML OUTPUT ----------
    history_html = "".join(
        f"<p><b>User {c['author_id']}:</b><br>{c.get('plain_body','')}</p>"
        for c in comments
    )

    html = f"""
    <h2>TICKET #{ticket['id']}</h2>
    <b>Status:</b> {ticket['status']}<br>
    <b>Priority:</b> {ticket.get('priority')}<br><br>

    <h3>Description</h3>
    <pre>{ticket.get('description','')}</pre>

    <h3>Suggested Solution</h3>
    <pre>{summary}</pre>

    <h3>History</h3>
    {history_html}
    """

    return {
        "summary": summary,
        "related_tickets": related_tickets,
        "related_docs": related_docs,
        "html": html,
    }

# ---------------- HEALTH ----------------
@app.get("/")
def health():
    return {"status": "ok"}
