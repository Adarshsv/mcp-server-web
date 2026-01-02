import os
import sys
import base64
import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from duckduckgo_search import DDGS

# Optional: OpenAI for AI summary
try:
    import openai
    OPENAI_KEY = os.getenv("OPENAI_API_KEY")
    if OPENAI_KEY:
        openai.api_key = OPENAI_KEY
except ImportError:
    openai = None
    OPENAI_KEY = None

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
    if OPENAI_KEY:
        print("[STARTUP] OpenAI API available for AI summaries", file=sys.stderr)
    else:
        print("[STARTUP] OpenAI API key not set, AI summaries disabled", file=sys.stderr)

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

async def ai_summary_fallback(text: str):
    """Generate a short AI summary if OpenAI key is set"""
    if not OPENAI_KEY or not openai:
        return None
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": f"Summarize this issue with resolution suggestions:\n{text}"}],
            temperature=0.2,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"AI summary failed: {e}"

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

    history_text = " ".join(c.get("plain_body", "") for c in comments)
    combined_text = f"{ticket['subject']} {ticket.get('description','')} {history_text}"

    keywords = extract_keywords(combined_text)
    related_tickets = await search_tickets(keywords)
    related_docs = search_docs(keywords)

    # Fallback AI summary
    ai_summary = await ai_summary_fallback(combined_text) or "No AI summary available, fallback applied."

    # Confidence score (simple heuristic)
    confidence = min(1.0, max(0.1, len(related_tickets)/10 + len(related_docs)/10))

    # Structured summary
    summary = f"""
Issue Summary:
{ticket['subject']}

Observed Behavior:
Found {len(comments)} comments describing the problem.

Similar Issues:
{len(related_tickets)} related tickets found.

Documentation Insights:
{len(related_docs)} relevant docs found.

Suggested Resolution:
{ai_summary}

Confidence Score: {confidence:.2f}
"""

    history_html = "".join(
        f"<p><b>User {c['author_id']}:</b><br>{c.get('plain_body','')}</p>" for c in comments
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
        "confidence": confidence,
        "related_tickets": related_tickets,
        "related_docs": related_docs,
        "html": html,
    }

# ------------------ SEARCH ALL ------------------
@app.post("/search/all")
async def search_all(req: QueryRequest):
    tickets = await search_tickets(req.query)
    docs = search_docs(req.query)

    summary = f"Searched tickets and docs for: {req.query}\nTickets: {len(tickets)}, Docs: {len(docs)}"
    return {
        "query": req.query,
        "summary": summary,
        "tickets": tickets,
        "docs": docs,
    }

# ------------------ PDF EXPORT ------------------
@app.post("/ticket/html-to-pdf")
async def html_to_pdf(req: dict):
    """Return PDF bytes from HTML content"""
    from weasyprint import HTML
    html_content = req.get("html")
    if not html_content:
        raise HTTPException(400, "Missing HTML content")

    pdf_bytes = HTML(string=html_content).write_pdf()
    return Response(content=pdf_bytes, media_type="application/pdf")

# ------------------ DEBUG ------------------
@app.get("/debug/env")
def debug_env():
    return {
        "email": bool(os.getenv("ZENDESK_EMAIL")),
        "token": bool(os.getenv("ZENDESK_API_TOKEN")),
        "subdomain": ZENDESK_SUBDOMAIN,
    }

@app.get("/debug/zendesk")
def debug_zendesk():
    return {
        "email": os.getenv("ZENDESK_EMAIL"),
        "token": os.getenv("ZENDESK_API_TOKEN"),
        "subdomain": ZENDESK_SUBDOMAIN
    }

# ------------------ HEALTH ------------------
@app.get("/")
def health():
    return {"status": "ok"}
