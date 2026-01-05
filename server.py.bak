import os
import sys
import base64
import asyncio
import re
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from duckduckgo_search import DDGS
from openai import OpenAI

# ---------------- ENV VALIDATION ----------------
REQUIRED_ENVS = [
    "ZENDESK_EMAIL",
    "ZENDESK_API_TOKEN",
    "ZENDESK_SUBDOMAIN",
    "OPENAI_API_KEY",
]

missing = [e for e in REQUIRED_ENVS if not os.getenv(e)]
if missing:
    raise RuntimeError(f"Missing environment variables: {missing}")

ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN")

ai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---------------- APP ----------------
app = FastAPI(title="CAST Ticket Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- MODELS ----------------
class TicketRequest(BaseModel):
    ticket_id: int

class QueryRequest(BaseModel):
    query: str

# ---------------- ZENDESK AUTH ----------------
def zendesk_headers():
    auth = f"{os.getenv('ZENDESK_EMAIL')}/token:{os.getenv('ZENDESK_API_TOKEN')}"
    encoded = base64.b64encode(auth.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }

# ---------------- HELPERS ----------------
async def get_ticket_comments(ticket_id: int):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json",
            headers=zendesk_headers(),
        )
        r.raise_for_status()
        return "\n".join(
            c["plain_body"]
            for c in r.json().get("comments", [])
            if c.get("plain_body")
        )

async def search_similar_tickets(query: str):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
            headers=zendesk_headers(),
            params={
                "query": f"type:ticket status:solved {query}"
            },
        )
        r.raise_for_status()
        results = r.json().get("results", [])[:5]

        tickets = []
        resolutions = ""

        for t in results:
            tickets.append({
                "id": t["id"],
                "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}"
            })
            resolutions += await get_ticket_comments(t["id"])[-1500:]

        return tickets, resolutions

def search_cast_docs(query: str):
    docs = []
    try:
        with DDGS() as ddgs:
            results = ddgs.text(
                f"site:doc.castsoftware.com {query}",
                max_results=5
            )
            for r in results:
                docs.append({
                    "title": r["title"],
                    "url": r["href"]
                })
    except Exception:
        pass
    return docs

def ai_analyze(context: str):
    response = ai_client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a CAST product support expert.\n"
                    "Summarize the issue clearly and extract the concrete resolution.\n"
                    "Respond strictly in this format:\n\n"
                    "Summary:\n...\n\nResolution:\n..."
                ),
            },
            {"role": "user", "content": context},
        ],
    )

    text = response.choices[0].message.content.strip()

    summary = re.search(r"Summary:(.*?)(Resolution:|$)", text, re.S)
    resolution = re.search(r"Resolution:(.*)", text, re.S)

    return {
        "summary": summary.group(1).strip() if summary else text,
        "resolution": resolution.group(1).strip() if resolution else "No clear resolution identified."
    }

# ---------------- CORE LOGIC ----------------
async def analyze_ticket(ticket_id: int):
    comments = await get_ticket_comments(ticket_id)
    similar_tickets, resolved_context = await search_similar_tickets(comments[:200])
    docs = search_cast_docs(comments[:200])

    ai_context = f"""
TICKET COMMENTS:
{comments}

SIMILAR RESOLVED TICKETS:
{resolved_context}
"""

    ai_result = ai_analyze(ai_context)

    confidence = round(min(0.4 + len(similar_tickets) * 0.15, 0.9), 2)

    return {
        "summary": ai_result["summary"],
        "recommended_solution": ai_result["resolution"],
        "confidence": confidence,
        "related_tickets": similar_tickets,
        "related_docs": docs,
    }

# ---------------- ROUTES ----------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    return await analyze_ticket(req.ticket_id)

@app.post("/search/docs")
async def search_docs(req: QueryRequest):
    return {"related_docs": search_cast_docs(req.query)}

# ---------------- UI ----------------
@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<title>CAST Ticket Analyzer</title>
<style>
body { font-family: Arial; max-width: 900px; margin: auto; padding: 20px; }
button { padding: 10px; background: #007bff; color: white; border: none; }
.card { background: #f9f9f9; padding: 15px; margin-top: 15px; }
</style>
</head>
<body>
<h1>CAST Ticket Analyzer</h1>

<input id="ticket" placeholder="Ticket ID">
<button onclick="run()">Analyze</button>

<div id="out"></div>

<script>
async function run(){
  const id = document.getElementById("ticket").value;
  const r = await fetch("/ticket/details",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({ticket_id:Number(id)})
  });
  const d = await r.json();

  document.getElementById("out").innerHTML = `
    <div class="card"><b>Summary</b><pre>${d.summary}</pre></div>
    <div class="card"><b>Confidence</b>: ${d.confidence}</div>
    <div class="card"><b>Recommended Solution</b><pre>${d.recommended_solution}</pre></div>
    <div class="card"><b>Related Tickets</b>${
      d.related_tickets.map(t=>`<p><a href="${t.url}" target="_blank">${t.id}</a></p>`).join("")
    }</div>
    <div class="card"><b>Documentation</b>${
      d.related_docs.map(d=>`<p><a href="${d.url}" target="_blank">${d.title}</a></p>`).join("")
    }</div>
  `;
}
</script>
</body>
</html>
"""
