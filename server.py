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

# ---------- OPTIONAL OPENAI ----------
openai_client = None
if os.getenv("OPENAI_API_KEY"):
    try:
        from openai import OpenAI
        openai_client = OpenAI()
    except Exception as e:
        print("[WARN] OpenAI not initialized:", e, file=sys.stderr)

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
async def startup_event():
    print("[STARTUP] MCP Web API running", file=sys.stderr)

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
        "User-Agent": "MCP-Web"
    }

# ------------------ MODELS ------------------
class TicketRequest(BaseModel):
    ticket_id: int

class QueryRequest(BaseModel):
    query: str

# ------------------ HELPERS ------------------
def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def extract_comments(comments):
    collected = []
    for c in comments:
        body = c.get("plain_body") or c.get("body") or ""
        body = clean_text(body)
        if body:
            collected.append(body)
    return " ".join(collected)

def shorten(text, max_sentences=4):
    parts = re.split(r"\. |\n", text)
    return ". ".join(parts[:max_sentences]).strip()

# ------------------ OPENAI SUMMARY ------------------
async def ai_summary(text):
    if not openai_client or not text:
        return None
    try:
        response = await asyncio.to_thread(
            openai_client.responses.create,
            model="gpt-4.1-mini",
            input=f"""
Summarize the following CAST support ticket issue clearly
and suggest a likely fix.

{text}
"""
        )
        return response.output_text.strip()
    except Exception as e:
        print("[OpenAI ERROR]", e, file=sys.stderr)
        return None

# ------------------ DOC SEARCH ------------------
def fetch_docs(query):
    docs = []
    if not query:
        return docs

    try:
        with DDGS() as ddgs:
            results = ddgs.text(
                f"CAST AIP analyzer {query} error",
                max_results=5
            )
            for r in results:
                docs.append({
                    "title": r.get("title"),
                    "url": r.get("href")
                })
    except Exception:
        pass

    return docs

# ------------------ CORE LOGIC ------------------
async def generate_summary(ticket_id=None, query=None):
    related_tickets = []
    related_docs = []
    raw_text = ""

    async with httpx.AsyncClient(timeout=25) as client:
        headers = zendesk_headers()

        # ----- Ticket-based -----
        if ticket_id:
            url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json"
            r = await client.get(url, headers=headers)
            comments = r.json().get("comments", [])
            raw_text = extract_comments(comments)

            related_tickets.append({
                "id": ticket_id,
                "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{ticket_id}"
            })

        # ----- Search-based -----
        if query:
            search = await client.get(
                f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
                headers=headers,
                params={"query": f"type:ticket {query}"}
            )
            results = search.json().get("results", [])[:5]

            for t in results:
                related_tickets.append({
                    "id": t["id"],
                    "subject": t["subject"],
                    "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}"
                })

        # ----- DOCS -----
        related_docs = fetch_docs(query or "")

    # ----- SUMMARY -----
    ai_text = await ai_summary(raw_text)
    observed = (
        ai_text
        or shorten(raw_text)
        or "Issue reported but detailed behavior not captured in comments."
    )

    # ----- RECOMMENDATION -----
    if ai_text:
        recommendation = "Apply the configuration or product fix suggested above."
    elif related_docs:
        recommendation = "Follow the linked CAST documentation for the recommended workaround."
    elif related_tickets:
        recommendation = "Apply the solution used in similar resolved tickets."
    else:
        recommendation = "Collect logs and escalate for deeper investigation."

    confidence = round(min(0.4 + len(related_tickets) * 0.15, 0.9), 2)

    return {
        "summary": f"Observed Behavior:\n{observed}",
        "confidence": confidence,
        "related_tickets": related_tickets,
        "related_docs": related_docs,
        "recommended_solution": recommendation
    }

# ------------------ API ------------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    return await generate_summary(ticket_id=req.ticket_id)

@app.post("/search/all")
async def search_all(req: QueryRequest):
    return await generate_summary(query=req.query)

# ------------------ UI ------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<title>CAST Support Dashboard</title>
<style>
body { font-family: Arial; background:#f4f4f4; padding:20px; max-width:1000px; margin:auto; }
.card { background:#fff; padding:15px; margin-top:15px; border-radius:8px; }
button { padding:10px; background:#007bff; color:white; border:none; }
input { padding:10px; width:100%; max-width:400px; }
</style>
</head>
<body>
<h1>CAST Ticket Analyzer</h1>

<input id="ticket_id" type="number" placeholder="Ticket ID"><br><br>
<input id="query" placeholder="Search keywords"><br><br>
<button onclick="go()">Analyze</button>

<div id="result"></div>

<script>
async function go() {
  let ticket = document.getElementById("ticket_id").value;
  let query = document.getElementById("query").value;
  let url = ticket ? "/ticket/details" : "/search/all";
  let body = ticket ? {ticket_id:parseInt(ticket)} : {query};

  document.getElementById("result").innerHTML="Loading...";

  let r = await fetch(url,{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)
  });

  let d = await r.json();

  let html = `<div class="card"><pre>${d.summary}</pre></div>`;
  html += `<div class="card"><b>Confidence:</b> ${d.confidence}</div>`;

  html += `<div class="card"><b>Related Tickets</b><br>`;
  (d.related_tickets||[]).forEach(t=>{
    html+=`<a target=_blank href="${t.url}">${t.subject||t.id}</a><br>`;
  });
  html += `</div>`;

  html += `<div class="card"><b>Documentation</b><br>`;
  (d.related_docs||[]).forEach(x=>{
    html+=`<a target=_blank href="${x.url}">${x.title}</a><br>`;
  });
  html += `</div>`;

  html += `<div class="card"><b>Recommended Solution</b><br>${d.recommended_solution}</div>`;

  document.getElementById("result").innerHTML=html;
}
</script>
</body>
</html>
"""
