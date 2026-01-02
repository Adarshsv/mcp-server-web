import os
import sys
import base64
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from duckduckgo_search import DDGS
from collections import Counter

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

# ------------------ STARTUP LOG ------------------
@app.on_event("startup")
async def startup():
    print("[STARTUP] Zendesk Email:", bool(os.getenv("ZENDESK_EMAIL")), file=sys.stderr)
    print("[STARTUP] Zendesk Token:", bool(os.getenv("ZENDESK_API_TOKEN")), file=sys.stderr)
    print("[STARTUP] Subdomain:", ZENDESK_SUBDOMAIN, file=sys.stderr)

# ------------------ MODELS ------------------
class TicketRequest(BaseModel):
    ticket_id: int

class SearchRequest(BaseModel):
    query: str

# ------------------ HELPERS ------------------
def zendesk_headers():
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")

    if not email or not token:
        raise HTTPException(500, "Zendesk credentials not set")

    auth = base64.b64encode(f"{email}/token:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json"
    }

def ticket_link(ticket_id):
    return f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{ticket_id}"

def summarise_comments(comments):
    texts = [c.get("plain_body", "") for c in comments if c.get("plain_body")]
    if not texts:
        return "No investigation comments available."

    keywords = Counter(" ".join(texts).lower().split())
    key_terms = ", ".join([k for k, _ in keywords.most_common(5)])

    return (
        f"Investigation involved {len(texts)} comments.\n"
        f"Frequent topics: {key_terms}."
    )

def extract_resolution(comments):
    for c in reversed(comments):
        body = c.get("plain_body", "").lower()
        if any(k in body for k in ["resolved", "fixed", "upgrade", "solution", "workaround"]):
            return c.get("plain_body")[:800]
    return "Resolution not explicitly documented. Recommendation inferred from discussion."

def generate_kb(ticket, resolution, docs):
    return f"""
Title:
{ticket['subject']}

Problem:
{ticket['description']}

Root Cause:
Product limitation or deprecated dependency.

Resolution:
{resolution}

Recommended Action:
Upgrade to supported version or apply documented workaround.

References:
{docs or "No official docs referenced."}

Applies To:
CAST AIP
""".strip()

def search_docs(query):
    with DDGS() as ddgs:
        return list(ddgs.text(f"site:doc.castsoftware.com {query}", max_results=3))

# ------------------ ROUTES ------------------
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

    if t.status_code != 200:
        return {"error": "Ticket not found"}

    ticket = t.json()["ticket"]
    comments = c.json().get("comments", [])

    comment_summary = summarise_comments(comments)
    resolution = extract_resolution(comments)

    related_search = f"type:ticket {ticket['subject']}"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
            headers=headers,
            params={"query": related_search}
        )

    related = [
        {
            "id": x["id"],
            "subject": x["subject"],
            "status": x["status"],
            "link": ticket_link(x["id"])
        }
        for x in r.json().get("results", []) if x["id"] != ticket["id"]
    ][:5]

    docs = search_docs(ticket["subject"])
    docs_summary = "\n".join(d["title"] for d in docs) if docs else ""

    kb = generate_kb(ticket, resolution, docs_summary)

    html = f"""
<h2>TICKET #{ticket['id']}</h2>
<b>Status:</b> {ticket['status']}<br><br>

<h3>Description</h3>
<pre>{ticket['description']}</pre>

<h3>Summary</h3>
<pre>{comment_summary}</pre>

<h3>Resolution</h3>
<pre>{resolution}</pre>

<h3>Related Tickets</h3>
<ul>
{''.join(f"<li><a href='{t['link']}'>#{t['id']} - {t['subject']}</a></li>" for t in related)}
</ul>

<h3>Suggested KB Article</h3>
<pre>{kb}</pre>
"""

    return {
        "summary": f"{comment_summary}\n\nResolution:\n{resolution}",
        "confidence": round(min(1.0, 0.3 + (len(related) * 0.15)), 2),
        "related_tickets": related,
        "kb_draft": kb,
        "html": html
    }

@app.post("/search/all")
async def search_all(req: SearchRequest):
    headers = zendesk_headers()

    async with httpx.AsyncClient() as client:
        tickets = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
            headers=headers,
            params={"query": f"type:ticket {req.query}"}
        )

    docs = search_docs(req.query)

    summary = (
        f"Search '{req.query}' found "
        f"{len(tickets.json().get('results', []))} tickets "
        f"and {len(docs)} documentation references."
    )

    return {
        "query": req.query,
        "summary": summary,
        "tickets": tickets.json(),
        "docs": docs
    }

@app.get("/")
def health():
    return {"status": "ok"}
