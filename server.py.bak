import asyncio
import base64
import os
import re
import functools
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx
from openai import OpenAI
from duckduckgo_search import DDGS
from asyncio import to_thread

# ===================== ENV =====================
REQUIRED_ENVS = [
    "ZENDESK_EMAIL",
    "ZENDESK_API_TOKEN",
    "ZENDESK_SUBDOMAIN",
]

for e in REQUIRED_ENVS:
    if not os.getenv(e):
        print(f"[WARN] {e} not set")

ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL", "")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN", "")
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "")

# ===================== APP =====================
app = FastAPI(title="CAST Ticket Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== CLIENTS =====================
async_client = httpx.AsyncClient(timeout=20)

@app.on_event("shutdown")
async def shutdown():
    await async_client.aclose()

# ===================== MODELS =====================
class UnifiedRequest(BaseModel):
    input: str  # ticket id OR text

# ===================== HELPERS =====================
def zendesk_headers():
    token = f"{ZENDESK_EMAIL}/token:{ZENDESK_API_TOKEN}"
    encoded = base64.b64encode(token.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }

def expand_doc_query(query: str) -> str:
    if len(query.split()) < 3:
        return f"CAST AIP {query} rule violation analysis dashboard"
    return f"CAST AIP {query}"

# ===================== DOC SEARCH (FIRST) =====================
def search_cast_docs(query: str):
    docs = []
    expanded = expand_doc_query(query)
    q = f"{expanded} site:doc.castsoftware.com"

    try:
        with DDGS() as ddgs:
            for r in ddgs.text(q, max_results=8):
                docs.append({
                    "title": r.get("title", "CAST Documentation"),
                    "url": r.get("href"),
                    "comment": f"Relevant to '{query}'"
                })
    except Exception as e:
        print("Doc search failed:", e)

    if not docs:
        return [{
            "title": "CAST AIP Documentation Home",
            "url": "https://doc.castsoftware.com/",
            "comment": "General CAST documentation"
        }]

    return docs[:3]

# ===================== ZENDESK =====================
async def get_ticket_comments(ticket_id: int):
    r = await async_client.get(
        f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json",
        headers=zendesk_headers(),
    )
    r.raise_for_status()
    return "\n".join(c["plain_body"] for c in r.json()["comments"])

async def search_related_tickets(query: str, exclude_id: int | None):
    zendesk_query = f"type:ticket status:solved {query}"
    r = await async_client.get(
        f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
        headers=zendesk_headers(),
        params={"query": zendesk_query}
    )
    r.raise_for_status()

    results = []
    for t in r.json().get("results", []):
        if exclude_id and t["id"] == exclude_id:
            continue
        results.append({
            "id": t["id"],
            "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}",
            "comment": t.get("description", "")[:300]
        })
        if len(results) == 3:
            break

    return results

# ===================== AI (LAST, OPTIONAL) =====================
def ai_analyze(text: str):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"summary": "", "resolution": ""}

    try:
        client = OpenAI(api_key=api_key)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=[
                {"role": "system", "content": "You are a CAST support expert. Summarize and give resolution."},
                {"role": "user", "content": text},
            ],
        )
        return {
            "summary": r.choices[0].message.content,
            "resolution": ""
        }
    except Exception:
        return {"summary": "", "resolution": ""}

# ===================== CORE =====================
async def analyze_input(value: str):
    # DOCS FIRST
    docs = await to_thread(search_cast_docs, value)

    # ticket or text?
    if value.isdigit():
        ticket_id = int(value)
        comments = await get_ticket_comments(ticket_id)
        tickets = await search_related_tickets(value, ticket_id)
        ai = await to_thread(ai_analyze, comments)
    else:
        tickets = await search_related_tickets(value, None)
        ai = await to_thread(ai_analyze, value)

    confidence = round(min(0.4 + len(tickets) * 0.15, 0.9), 2)

    return {
        "summary": ai.get("summary", ""),
        "recommended_solution": ai.get("resolution", ""),
        "confidence": confidence,
        "related_tickets": tickets,
        "related_docs": docs,
    }

# ===================== ROUTES =====================
@app.post("/analyze")
async def analyze(req: UnifiedRequest):
    return await analyze_input(req.input)

@app.get("/ping")
def ping():
    return {"status": "ok"}

# ===================== UI =====================
@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<title>CAST Ticket Analyzer</title>
<style>
body { font-family: Arial; max-width: 900px; margin: auto; padding: 20px; }
input,button { padding: 10px; font-size: 16px; }
.card { background: #f9f9f9; padding: 15px; margin-top: 15px; }
</style>
</head>
<body>

<h2>CAST Ticket Analyzer</h2>
<input id="q" placeholder="Ticket ID or search text" style="width:70%">
<button onclick="run()">Analyze</button>

<div id="out"></div>

<script>
async function run(){
  out.innerHTML="Loading...";
  const r = await fetch("/analyze",{
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ input: q.value })
  });
  const d = await r.json();

  out.innerHTML = `
    <div class="card"><b>Summary</b><pre>${d.summary||""}</pre></div>
    <div class="card"><b>Confidence:</b> ${d.confidence}</div>
    <div class="card"><b>Related Tickets</b>${
      (d.related_tickets||[]).map(t=>`<p><a target=_blank href="${t.url}">${t.id}</a><br>${t.comment}</p>`).join("")
    }</div>
    <div class="card"><b>Documentation</b>${
      (d.related_docs||[]).map(x=>`<p><a target=_blank href="${x.url}">${x.title}</a><br>${x.comment}</p>`).join("")
    }</div>`;
}
</scriptimport asyncio
import base64
import os
import re
import functools
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx
from openai import OpenAI
from duckduckgo_search import DDGS
from asyncio import to_thread

# ===================== ENV =====================
REQUIRED_ENVS = [
    "ZENDESK_EMAIL",
    "ZENDESK_API_TOKEN",
    "ZENDESK_SUBDOMAIN",
]

for e in REQUIRED_ENVS:
    if not os.getenv(e):
        print(f"[WARN] {e} not set")

ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL", "")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN", "")
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "")

# ===================== APP =====================
app = FastAPI(title="CAST Ticket Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== CLIENTS =====================
async_client = httpx.AsyncClient(timeout=20)

@app.on_event("shutdown")
async def shutdown():
    await async_client.aclose()

# ===================== MODELS =====================
class UnifiedRequest(BaseModel):
    input: str  # ticket id OR text

# ===================== HELPERS =====================
def zendesk_headers():
    token = f"{ZENDESK_EMAIL}/token:{ZENDESK_API_TOKEN}"
    encoded = base64.b64encode(token.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }

def expand_doc_query(query: str) -> str:
    if len(query.split()) < 3:
        return f"CAST AIP {query} rule violation analysis dashboard"
    return f"CAST AIP {query}"

# ===================== DOC SEARCH (FIRST) =====================
def search_cast_docs(query: str):
    docs = []
    expanded = expand_doc_query(query)
    q = f"{expanded} site:doc.castsoftware.com"

    try:
        with DDGS() as ddgs:
            for r in ddgs.text(q, max_results=8):
                docs.append({
                    "title": r.get("title", "CAST Documentation"),
                    "url": r.get("href"),
                    "comment": f"Relevant to '{query}'"
                })
    except Exception as e:
        print("Doc search failed:", e)

    if not docs:
        return [{
            "title": "CAST AIP Documentation Home",
            "url": "https://doc.castsoftware.com/",
            "comment": "General CAST documentation"
        }]

    return docs[:3]

# ===================== ZENDESK =====================
async def get_ticket_comments(ticket_id: int):
    r = await async_client.get(
        f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json",
        headers=zendesk_headers(),
    )
    r.raise_for_status()
    return "\n".join(c["plain_body"] for c in r.json()["comments"])

async def search_related_tickets(query: str, exclude_id: int | None):
    zendesk_query = f"type:ticket status:solved {query}"
    r = await async_client.get(
        f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
        headers=zendesk_headers(),
        params={"query": zendesk_query}
    )
    r.raise_for_status()

    results = []
    for t in r.json().get("results", []):
        if exclude_id and t["id"] == exclude_id:
            continue
        results.append({
            "id": t["id"],
            "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}",
            "comment": t.get("description", "")[:300]
        })
        if len(results) == 3:
            break

    return results

# ===================== AI (LAST, OPTIONAL) =====================
def ai_analyze(text: str):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"summary": "", "resolution": ""}

    try:
        client = OpenAI(api_key=api_key)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=[
                {"role": "system", "content": "You are a CAST support expert. Summarize and give resolution."},
                {"role": "user", "content": text},
            ],
        )
        return {
            "summary": r.choices[0].message.content,
            "resolution": ""
        }
    except Exception:
        return {"summary": "", "resolution": ""}

# ===================== CORE =====================
async def analyze_input(value: str):
    # DOCS FIRST
    docs = await to_thread(search_cast_docs, value)

    # ticket or text?
    if value.isdigit():
        ticket_id = int(value)
        comments = await get_ticket_comments(ticket_id)
        tickets = await search_related_tickets(value, ticket_id)
        ai = await to_thread(ai_analyze, comments)
    else:
        tickets = await search_related_tickets(value, None)
        ai = await to_thread(ai_analyze, value)

    confidence = round(min(0.4 + len(tickets) * 0.15, 0.9), 2)

    return {
        "summary": ai.get("summary", ""),
        "recommended_solution": ai.get("resolution", ""),
        "confidence": confidence,
        "related_tickets": tickets,
        "related_docs": docs,
    }

# ===================== ROUTES =====================
@app.post("/analyze")
async def analyze(req: UnifiedRequest):
    return await analyze_input(req.input)

@app.get("/ping")
def ping():
    return {"status": "ok"}

# ===================== UI =====================
@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<title>CAST Ticket Analyzer</title>
<style>
body { font-family: Arial; max-width: 900px; margin: auto; padding: 20px; }
input,button { padding: 10px; font-size: 16px; }
.card { background: #f9f9f9; padding: 15px; margin-top: 15px; }
</style>
</head>
<body>

<h2>CAST Ticket Analyzer</h2>
<input id="q" placeholder="Ticket ID or search text" style="width:70%">
<button onclick="run()">Analyze</button>

<div id="out"></div>

<script>
async function run(){
  out.innerHTML="Loading...";
  const r = await fetch("/analyze",{
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ input: q.value })
  });
  const d = await r.json();

  out.innerHTML = `
    <div class="card"><b>Summary</b><pre>${d.summary||""}</pre></div>
    <div class="card"><b>Confidence:</b> ${d.confidence}</div>
    <div class="card"><b>Related Tickets</b>${
      (d.related_tickets||[]).map(t=>`<p><a target=_blank href="${t.url}">${t.id}</a><br>${t.comment}</p>`).join("")
    }</div>
    <div class="card"><b>Documentation</b>${
      (d.related_docs||[]).map(x=>`<p><a target=_blank href="${x.url}">${x.title}</a><br>${x.comment}</p>`).join("")
    }</div>`;
}
</script>
</body>
</html>
"""

# ===================== START =====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

</body>
</html>
"""

# ===================== START =====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
