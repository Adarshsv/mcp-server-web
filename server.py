import os
import sys
import base64
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from duckduckgo_search import DDGS

# ------------------ APP SETUP ------------------
app = FastAPI(title="MCP Web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "castsoftware")
API_KEY = os.getenv("API_KEY")  # optional (DEV friendly)

# ------------------ STARTUP LOG ------------------
@app.on_event("startup")
async def startup():
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")

    print(f"[STARTUP] Zendesk Email Loaded: {bool(email)}", file=sys.stderr)
    print(f"[STARTUP] Zendesk API Token Loaded: {bool(token)}", file=sys.stderr)
    print(f"[STARTUP] Zendesk Subdomain: {ZENDESK_SUBDOMAIN}", file=sys.stderr)
    print(f"[STARTUP] API_KEY enabled: {bool(API_KEY)}", file=sys.stderr)

# ------------------ API KEY (OPTIONAL) ------------------
@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if request.url.path.startswith("/debug") or request.url.path in ["/", "/version"]:
        return await call_next(request)

    if not API_KEY:
        return await call_next(request)

    if request.headers.get("x-api-key") != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return await call_next(request)

# ------------------ HELPERS ------------------
def zendesk_headers():
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")

    if not email or not token:
        raise HTTPException(status_code=500, detail="Zendesk credentials not set in environment")

    auth = f"{email}/token:{token}"
    encoded = base64.b64encode(auth.encode()).decode()

    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "User-Agent": "MCP-Web"
    }

def claude_style_summary(ticket, comments):
    points = []
    if ticket.get("status"):
        points.append(f"• Status: {ticket['status']}")
    if ticket.get("priority"):
        points.append(f"• Priority: {ticket['priority']}")
    if ticket.get("assignee_id"):
        points.append("• Assigned to support engineer")
    if comments:
        points.append(f"• {len(comments)} discussion updates")

    return "\n".join(points)

def ticket_html(ticket, comments):
    html = f"""
    <h2>🎫 Ticket #{ticket['id']}</h2>
    <b>Subject:</b> {ticket['subject']}<br>
    <b>Status:</b> {ticket['status']}<br><br>

    <h3>Description</h3>
    <pre>{ticket['description']}</pre>

    <h3>History</h3>
    """

    for c in comments:
        html += f"""
        <div style="margin-bottom:15px;">
            <b>User {c['author_id']}:</b>
            <pre>{c.get('plain_body','')}</pre>
        </div>
        """

    return html

# ------------------ MODELS ------------------
class QueryRequest(BaseModel):
    query: str

class TicketRequest(BaseModel):
    ticket_id: int

# ------------------ DEBUG ------------------
@app.get("/debug/env")
def debug_env():
    return {
        "email": bool(os.getenv("ZENDESK_EMAIL")),
        "token": bool(os.getenv("ZENDESK_API_TOKEN")),
        "subdomain": ZENDESK_SUBDOMAIN
    }

@app.get("/debug/zendesk")
def debug_zendesk():
    return {
        "email": os.getenv("ZENDESK_EMAIL"),
        "token": os.getenv("ZENDESK_API_TOKEN"),
        "subdomain": ZENDESK_SUBDOMAIN
    }

# ------------------ SEARCH DOCS ------------------
@app.post("/search/docs")
async def search_docs(req: QueryRequest):
    with DDGS() as ddgs:
        results = ddgs.text(
            f"site:doc.castsoftware.com {req.query}",
            max_results=5
        )

    if not results:
        return {"count": 0, "results": []}

    docs = [
        {
            "title": r["title"],
            "link": r["href"],
            "snippet": r["body"]
        }
        for r in results
    ]

    return {"count": len(docs), "results": docs}

# ------------------ SEARCH TICKETS (INCL HISTORY) ------------------
@app.post("/search/tickets")
async def search_tickets(req: QueryRequest):
    headers = zendesk_headers()
    query = f"type:ticket {req.query}"

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
            params={"query": query},
            headers=headers
        )

    results = resp.json().get("results", [])
    tickets = [
        {
            "id": t["id"],
            "subject": t["subject"],
            "status": t["status"]
        }
        for t in results[:5]
    ]

    return {"count": len(results), "results": tickets}

# ------------------ COMBINED SEARCH ------------------
@app.post("/search/all")
async def search_all(req: QueryRequest):
    tickets = await search_tickets(req)
    docs = await search_docs(req)

    return {
        "query": req.query,
        "tickets": tickets,
        "docs": docs
    }

# ------------------ TICKET DETAILS ------------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    headers = zendesk_headers()

    async with httpx.AsyncClient(timeout=20) as client:
        t = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}.json",
            headers=headers
        )
        c = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}/comments.json",
            headers=headers
        )

    ticket = t.json()["ticket"]
    comments = c.json().get("comments", [])

    return {
        "summary": claude_style_summary(ticket, comments),
        "html": ticket_html(ticket, comments),
        "raw": ticket
    }

# ------------------ HEALTH ------------------
@app.get("/")
def health():
    return {"status": "ok"}

@app.get("/version")
def version():
    return {"version": "1.0.0"}
