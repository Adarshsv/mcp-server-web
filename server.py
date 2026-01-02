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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # optional

# ----------------- APP -----------------
app = FastAPI(title="MCP Web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- MODELS -----------------
class QueryRequest(BaseModel):
    query: str

class TicketRequest(BaseModel):
    ticket_id: int

# ----------------- ZENDESK AUTH -----------------
def zendesk_headers():
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")

    if not email or not token:
        raise HTTPException(500, "Zendesk credentials missing")

    auth = base64.b64encode(f"{email}/token:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json"
    }

# ----------------- FALLBACK SUMMARY -----------------
def solution_summary(query, tickets_count, docs_count):
    lines = [
        f"Search keyword: '{query}'",
        f"- Matching tickets found: {tickets_count}",
        f"- Matching documentation pages: {docs_count}",
    ]

    if tickets_count > 0:
        lines.append(
            "Recommended action: Review recent ticket comments and resolutions. "
            "Similar past issues may already contain a workaround or fix."
        )
    elif docs_count > 0:
        lines.append(
            "Recommended action: Refer to CAST documentation for configuration or known limitations."
        )
    else:
        lines.append(
            "Recommended action: No direct matches found. Consider refining keywords or escalating to R&D."
        )

    return "\n".join(lines)

# ----------------- DOC SEARCH -----------------
def search_docs(query: str):
    with DDGS() as ddgs:
        results = ddgs.text(
            f"site:doc.castsoftware.com {query}",
            max_results=5
        )

    docs = []
    for r in results:
        docs.append({
            "title": r["title"],
            "link": r["href"],
            "snippet": r["body"][:300]
        })

    return {
        "count": len(docs),
        "results": docs
    }

# ----------------- TICKET SEARCH (COMMENTS + HISTORY) -----------------
async def search_tickets(query: str):
    headers = zendesk_headers()

    params = {
        "query": f'type:ticket "{query}"',
        "include": "comment"
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
            headers=headers,
            params=params
        )

    results = resp.json().get("results", [])

    tickets = []
    for t in results[:10]:
        tickets.append({
            "id": t["id"],
            "subject": t.get("subject"),
            "status": t.get("status"),
            "snippet": (t.get("description") or "")[:300]
        })

    return {
        "count": len(results),
        "results": tickets
    }

# ----------------- SEARCH ALL -----------------
@app.post("/search/all")
async def search_all(req: QueryRequest):
    tickets = await search_tickets(req.query)
    docs = search_docs(req.query)

    summary = solution_summary(
        req.query,
        tickets["count"],
        docs["count"]
    )

    return {
        "query": req.query,
        "summary": summary,
        "tickets": tickets,
        "docs": docs
    }

# ----------------- TICKET DETAILS -----------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    headers = zendesk_headers()

    async with httpx.AsyncClient() as client:
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

    history = "\n\n".join(
        f"User {x['author_id']}:\n{x.get('plain_body','')}"
        for x in comments
    )

    return {
        "summary": solution_summary(
            ticket["subject"],
            len(comments),
            0
        ),
        "html": f"""
        <h2>TICKET #{ticket['id']}</h2>
        <b>Status:</b> {ticket['status']}<br>
        <b>Priority:</b> {ticket.get('priority')}<br><br>
        <h3>Description</h3>
        <pre>{ticket['description']}</pre>
        <h3>History</h3>
        <pre>{history}</pre>
        """
    }

@app.get("/")
def health():
    return {"status": "ok"}
