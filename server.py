import os
import sys
import base64
import httpx
import asyncio
import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from duckduckgo_search import DDGS

# ------------------ ENV SETUP ------------------
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "castsoftware")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ----------------- APP DEFINITION -----------------
app = FastAPI(title="MCP Web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------- STARTUP LOGGING --------
@app.on_event("startup")
async def startup_event():
    print("[STARTUP] Zendesk Email:", bool(os.getenv("ZENDESK_EMAIL")), file=sys.stderr)
    print("[STARTUP] Zendesk Token:", bool(os.getenv("ZENDESK_API_TOKEN")), file=sys.stderr)
    print("[STARTUP] OpenAI Enabled:", bool(OPENAI_API_KEY), file=sys.stderr)

# -------- Zendesk Auth --------
def zendesk_headers():
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")
    if not email or not token:
        raise HTTPException(status_code=500, detail="Zendesk credentials missing")
    auth = base64.b64encode(f"{email}/token:{token}".encode()).decode()
    return {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}

# -------- Models --------
class QueryRequest(BaseModel):
    query: str

class TicketRequest(BaseModel):
    ticket_id: int

# -------- Text Helpers --------
def summarize_comments(comments):
    text = " ".join(
        (c.get("plain_body") or c.get("body") or "").replace("\n", " ")
        for c in comments[:8]
    )
    sentences = re.split(r"\. ", text)
    return ". ".join(sentences[:5]) + "."

def highlight(text, keywords):
    for k in keywords:
        text = re.sub(re.escape(k), f"<mark>{k}</mark>", text, flags=re.I)
    return text

# -------- OpenAI Summarizer (Optional) --------
async def ai_summary(text):
    if not OPENAI_API_KEY or not text.strip():
        return None

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "Summarize the issue clearly in 4 sentences."},
                        {"role": "user", "content": text}
                    ],
                    "temperature": 0.2
                },
            )
            return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None

# -------- Core Logic --------
async def generate_summary(query=None, ticket_ids=None):
    related_tickets, related_docs = [], []
    comment_text = ""

    async with httpx.AsyncClient(timeout=30) as client:
        headers = zendesk_headers()

        # --- Ticket Fetch ---
        if ticket_ids:
            for tid in ticket_ids:
                c = await client.get(
                    f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{tid}/comments.json",
                    headers=headers,
                )
                comments = c.json().get("comments", [])
                comment_text += summarize_comments(comments)
                related_tickets.append({
                    "id": tid,
                    "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{tid}"
                })

        # --- Keyword Search ---
        if query:
            r = await client.get(
                f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
                headers=headers,
                params={"query": f"type:ticket {query}"}
            )
            for t in r.json().get("results", [])[:5]:
                related_tickets.append({
                    "id": t["id"],
                    "subject": t["subject"],
                    "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}"
                })

        # --- Docs via DuckDuckGo (FIXED) ---
        if query:
            with DDGS() as ddgs:
                for d in ddgs.search(
                    f"site:doc.castsoftware.com {query}",
                    max_results=5
                ):
                    if d.get("href"):
                        related_docs.append({
                            "title": d.get("title", "CAST Documentation"),
                            "url": d["href"]
                        })
                related_docs = related_docs[:3]

    # --- AI Summary (if enabled) ---
    ai_result = await ai_summary(comment_text)

    observed = ai_result or comment_text or "No significant ticket history found."

    # --- Dynamic Recommendation ---
    recommendation = (
        "Review related tickets and apply known fixes."
        if related_tickets else
        "Check CAST documentation and validate configuration."
    )

    if related_docs:
        recommendation += " Refer to linked CAST documentation."

    confidence = round(min(0.4 + len(related_tickets) * 0.1 + len(related_docs) * 0.1, 0.95), 2)

    return {
        "summary": f"Observed Behavior: {observed}",
        "confidence": confidence,
        "related_tickets": related_tickets,
        "related_docs": related_docs,
        "recommended_solution": recommendation
    }

# -------- API Routes --------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    return await generate_summary(ticket_ids=[req.ticket_id])

@app.post("/search/all")
async def search_all(req: QueryRequest):
    return await generate_summary(query=req.query)

# -------- UI --------
@app.get("/", response_class=HTMLResponse)
def home():
    return open("ui.html").read() if os.path.exists("ui.html") else "<h2>UI missing</h2>"

@app.get("/health")
def health():
    return {"status": "ok"}
