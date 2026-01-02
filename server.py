import os
import sys
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from duckduckgo_search import DDGS
from dotenv import load_dotenv

# ------------------ ENV SETUP ------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

ZENDESK_COOKIE = os.getenv("ZENDESK_COOKIE")
ZENDESK_SUBDOMAIN = "castsoftware"

print("Cookie loaded:", bool(ZENDESK_COOKIE), file=sys.stderr)
# ----------------------------------------------

app = FastAPI(title="MCP Web API")

# -------- CORS (required for Vercel UI) --------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # safe for internal tools
    allow_methods=["*"],
    allow_headers=["*"],
)
# ----------------------------------------------

# -------- Request Models --------
class QueryRequest(BaseModel):
    query: str

class TicketRequest(BaseModel):
    ticket_id: int
# --------------------------------

@app.post("/search/docs")
async def search_docs(req: QueryRequest):
    """Search CAST documentation"""
    try:
        with DDGS() as ddgs:
            results = ddgs.text(
                f"site:doc.castsoftware.com {req.query}",
                max_results=5
            )

            if not results:
                return {"result": "No documentation found."}

            output = []
            for r in results:
                output.append(
                    f"Title: {r['title']}\n"
                    f"Link: {r['href']}\n"
                    f"Snippet: {r['body']}\n"
                    "----------------------"
                )

            return {"result": "\n".join(output)}

    except Exception as e:
        return {"result": f"Error: {e}"}


@app.post("/search/tickets")
async def search_tickets(req: QueryRequest):
    """Search Zendesk tickets"""
    if not ZENDESK_COOKIE:
        return {"result": "Zendesk cookie missing."}

    url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json"
    headers = {"Cookie": ZENDESK_COOKIE, "User-Agent": "MCP-Web"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url,
            params={"query": f"type:ticket {req.query}"},
            headers=headers,
        )

        if resp.status_code == 401:
            return {"result": "Zendesk cookie expired."}

        results = resp.json().get("results", [])
        if not results:
            return {"result": "No tickets found."}

        lines = [
            f"ID: {t['id']} | {t['subject']}"
            for t in results[:5]
        ]

        return {"result": "\n".join(lines)}


@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    """Get full ticket details"""
    if not ZENDESK_COOKIE:
        return {"result": "Zendesk cookie missing."}

    headers = {"Cookie": ZENDESK_COOKIE, "User-Agent": "MCP-Web"}
    async with httpx.AsyncClient() as client:
        t_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}.json",
            headers=headers,
        )

        if t_resp.status_code == 401:
            return {"result": "Zendesk cookie expired."}
        if t_resp.status_code == 404:
            return {"result": "Ticket not found."}

        ticket = t_resp.json()["ticket"]

        c_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}/comments.json",
            headers=headers,
        )

        comments = c_resp.json().get("comments", [])

        history = "\n\n".join(
            f"User {c['author_id']}:\n{c.get('plain_body','')}"
            for c in comments
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
