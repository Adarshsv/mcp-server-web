import os
import sys
import base64
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from duckduckgo_search import DDGS
from dotenv import load_dotenv

# ------------------ ENV SETUP ------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Uncomment for local testing
# load_dotenv(os.path.join(BASE_DIR, ".env"))

ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "castsoftware")

# ----------------- APP DEFINITION -----------------
app = FastAPI(title="MCP Web API")

# -------- CORS (required for internal tooling) --------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------- STARTUP LOGGING --------
@app.on_event("startup")
async def startup_event():
    email_loaded = bool(os.getenv("ZENDESK_EMAIL"))
    token_loaded = bool(os.getenv("ZENDESK_API_TOKEN"))
    subdomain = ZENDESK_SUBDOMAIN

    token_display = "****" + os.getenv("ZENDESK_API_TOKEN", "")[-4:] if token_loaded else None
    email_display = os.getenv("ZENDESK_EMAIL", "None") if email_loaded else "None"

    print(f"[STARTUP] Zendesk Email Loaded: {email_loaded} ({email_display})", file=sys.stderr)
    print(f"[STARTUP] Zendesk API Token Loaded: {token_loaded} ({token_display})", file=sys.stderr)
    print(f"[STARTUP] Zendesk Subdomain: {subdomain}", file=sys.stderr)

# -------- Helper: Zendesk Auth Header ----------
def zendesk_headers():
    """Reads Zendesk credentials from environment at request time"""
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")

    if not email or not token:
        raise HTTPException(status_code=500, detail="Zendesk credentials not set in environment")

    auth_str = f"{email}/token:{token}"
    encoded = base64.b64encode(auth_str.encode()).decode()

    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "User-Agent": "MCP-Web"
    }

# -------- Request Models --------
class QueryRequest(BaseModel):
    query: str

class TicketRequest(BaseModel):
    ticket_id: int

# -------- ROUTES --------
@app.get("/debug/env")
def debug_env():
    """Check if variables are loaded at startup"""
    return {
        "email": bool(os.getenv("ZENDESK_EMAIL")),
        "token": bool(os.getenv("ZENDESK_API_TOKEN")),
        "subdomain": ZENDESK_SUBDOMAIN,
    }

@app.get("/debug/zendesk")
def debug_zendesk():
    """Check live environment variables"""
    return {
        "email": os.getenv("ZENDESK_EMAIL"),
        "token": os.getenv("ZENDESK_API_TOKEN"),
        "subdomain": ZENDESK_SUBDOMAIN
    }

@app.post("/search/docs")
async def search_docs(req: QueryRequest):
    """Search CAST documentation"""
    try:
        with DDGS() as ddgs:
            results = ddgs.text(f"site:doc.castsoftware.com {req.query}", max_results=5)
            if not results:
                return {"result": "No documentation found."}

            output = [
                f"Title: {r['title']}\nLink: {r['href']}\nSnippet: {r['body']}\n----------------------"
                for r in results
            ]
            return {"result": "\n".join(output)}

    except Exception as e:
        return {"result": f"Error: {e}"}

@app.post("/search/tickets")
async def search_tickets(req: QueryRequest):
    """Search Zendesk tickets"""
    headers = zendesk_headers()
    url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params={"query": f"type:ticket {req.query}"}, headers=headers)

        if resp.status_code == 401:
            return {"result": "Zendesk authentication failed."}

        results = resp.json().get("results", [])
        if not results:
            return {"result": "No tickets found."}

        lines = [f"ID: {t['id']} | {t['subject']}" for t in results[:5]]
        return {"result": "\n".join(lines)}

@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    """Get full ticket details"""
    headers = zendesk_headers()

    async with httpx.AsyncClient() as client:
        t_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}.json",
            headers=headers,
        )

        if t_resp.status_code == 401:
            return {"result": "Zendesk authentication failed."}
        if t_resp.status_code == 404:
            return {"result": "Ticket not found."}

        ticket = t_resp.json()["ticket"]

        c_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}/comments.json",
            headers=headers,
        )

        comments = c_resp.json().get("comments", [])

        history = "\n\n".join(
            f"User {c['author_id']}:\n{c.get('plain_body','')}" for c in comments
        )

        output = (
            f"TICKET #{ticket['id']}\n"
            f"SUBJECT: {ticket['subject']}\n"
            f"STATUS: {ticket['status']}\n\n"
            f"DESCRIPTION:\n{ticket['description']}\n\n"
            f"HISTORY:\n{history}"
        )

        return {"result": output}

@app.get("/")
def health():
    return {"status": "ok"}
