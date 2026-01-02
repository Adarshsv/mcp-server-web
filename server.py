import os
import base64
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# -------------------------------------------------
# ENV
# -------------------------------------------------
ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN")
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN")
API_KEY = os.getenv("API_KEY")

IS_PROD = os.getenv("RAILWAY_ENVIRONMENT") == "production"

# -------------------------------------------------
# FASTAPI
# -------------------------------------------------
app = FastAPI(title="MCP Zendesk Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------
# SECURITY MIDDLEWARE
# -------------------------------------------------
@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if request.url.path.startswith("/debug") or request.url.path == "/version":
        return await call_next(request)

    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY not configured")

    api_key = request.headers.get("x-api-key")
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return await call_next(request)

# -------------------------------------------------
# HELPERS
# -------------------------------------------------
def zendesk_headers():
    if not all([ZENDESK_EMAIL, ZENDESK_API_TOKEN]):
        raise HTTPException(status_code=500, detail="Zendesk credentials missing")

    auth = f"{ZENDESK_EMAIL}/token:{ZENDESK_API_TOKEN}"
    encoded = base64.b64encode(auth.encode()).decode()

    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json"
    }

def summarize_ticket(ticket, comments):
    lines = [
        f"TICKET #{ticket['id']}: {ticket['subject']}",
        f"STATUS: {ticket['status']}",
        f"CREATED: {ticket['created_at']}",
        "",
        "RECENT ACTIVITY:"
    ]

    for c in comments[-3:]:
        body = c.get("plain_body", "").strip()
        if body:
            lines.append(f"- {body[:300]}")

    return "\n".join(lines)

def render_ticket_html(ticket, comments):
    history = "".join(
        f"""
        <li>
            <b>User {c['author_id']}:</b><br/>
            <pre>{c.get('plain_body','')}</pre>
        </li>
        """
        for c in comments
    )

    return f"""
    <h2>Ticket #{ticket['id']}</h2>
    <p><b>Status:</b> {ticket['status']}</p>
    <p><b>Priority:</b> {ticket.get('priority')}</p>
    <p><b>Subject:</b> {ticket['subject']}</p>

    <h3>Description</h3>
    <pre>{ticket['description']}</pre>

    <h3>Conversation</h3>
    <ul>{history}</ul>
    """

# -------------------------------------------------
# MODELS
# -------------------------------------------------
class TicketRequest(BaseModel):
    ticket_id: int

# -------------------------------------------------
# ROUTES
# -------------------------------------------------
@app.get("/version")
def version():
    return {
        "service": "mcp-server-web",
        "commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
        "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
        "environment": os.getenv("RAILWAY_ENVIRONMENT"),
    }

@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    ticket_url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}.json"
    comments_url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}/comments.json"

    async with httpx.AsyncClient(timeout=30) as client:
        ticket_res = await client.get(ticket_url, headers=zendesk_headers())
        comments_res = await client.get(comments_url, headers=zendesk_headers())

    if ticket_res.status_code != 200:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket = ticket_res.json()["ticket"]
    comments = comments_res.json()["comments"]

    return {
        "summary": summarize_ticket(ticket, comments),
        "html": render_ticket_html(ticket, comments),
        "raw": {
            "ticket": ticket,
            "comments": comments
        }
    }

# -------------------------------------------------
# DEBUG (NON-PROD ONLY)
# -------------------------------------------------
if not IS_PROD:
    @app.get("/debug/zendesk")
    def debug_zendesk():
        return {
            "email": bool(ZENDESK_EMAIL),
            "token": bool(ZENDESK_API_TOKEN),
            "subdomain": ZENDESK_SUBDOMAIN
        }
