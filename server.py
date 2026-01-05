import asyncio
import base64
import re
import os
import functools
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx
from openai import OpenAI
from duckduckgo_search import DDGS
from asyncio import to_thread

# ---------------- ENV ----------------
REQUIRED_ENVS = [
    "ZENDESK_EMAIL",
    "ZENDESK_API_TOKEN",
    "ZENDESK_SUBDOMAIN",
    "OPENAI_API_KEY",
]

for e in REQUIRED_ENVS:
    if not os.getenv(e):
        print(f"Warning: {e} is missing. API calls may fail.")

ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL", "")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN", "")
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "")

# ---------------- OPENAI (LAZY INIT) ----------------
def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[Warning] OPENAI_API_KEY not set. AI analysis will be skipped.")
        return None
    return OpenAI(api_key=api_key)

# ---------------- GLOBAL ASYNC CLIENT ----------------
async_client = httpx.AsyncClient(timeout=15)

async def shutdown_client():
    await async_client.aclose()

# ---------------- APP ----------------
app = FastAPI(title="CAST Ticket Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_event():
    await shutdown_client()

# ---------------- MODELS ----------------
class TicketRequest(BaseModel):
    ticket_id: int

class QueryRequest(BaseModel):
    query: str

# ---------------- HELPERS ----------------
def extract_keywords(text: str, max_words=8):
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text)
    blacklist = {"error", "issue", "problem", "unable", "failed", "ticket", "please"}
    keywords = [w for w in words if w.lower() not in blacklist]
    if not keywords:
        keywords = ["CAST"]
    return " ".join(keywords[:max_words])

# ---------------- ZENDESK ----------------
def zendesk_headers():
    auth = f"{ZENDESK_EMAIL}/token:{ZENDESK_API_TOKEN}"
    encoded = base64.b64encode(auth.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json"
    }

async def get_ticket_comments(ticket_id: int):
    r = await async_client.get(
        f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json",
        headers=zendesk_headers(),
    )
    r.raise_for_status()
    return "\n".join(
        c.get("plain_body", "") for c in r.json().get("comments", [])
    )

async def search_related_tickets(query: str, ticket_id: int):
    keywords = query.split() or ["CAST"]
    zendesk_query = f"type:ticket status:solved ({' OR '.join(keywords)})"
    print("Zendesk search query:", zendesk_query)

    r = await async_client.get(
        f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
        headers=zendesk_headers(),
        params={"query": zendesk_query, "sort_by": "updated_at", "sort_order": "desc"}
    )
    r.raise_for_status()

    results = r.json().get("results", [])
    related = []
    for t in results:
        if t["id"] == ticket_id:
            continue
        related.append({
            "id": t["id"],
            "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}"
        })
        if len(related) == 3:
            break

    if not related:
        r = await async_client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets.json",
            headers=zendesk_headers(),
            params={"status": "solved", "sort_by": "updated_at", "sort_order": "desc"}
        )
        r.raise_for_status()
        for t in r.json().get("tickets", [])[:3]:
            if t["id"] != ticket_id:
                related.append({
                    "id": t["id"],
                    "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}"
                })

    return related

# ---------------- DOC SEARCH ----------------
def search_cast_docs(query: str):
    docs = []
    query = query.strip() or "CAST AIP"
    ddg_query = f"CAST AIP {query} site:doc.castsoftware.com"
    try:
        with DDGS() as ddgs:
            results = ddgs.text(ddg_query, max_results=5)
            for r in results:
                docs.append({
                    "title": r.get("title", "Untitled"),
                    "url": r.get("href")
                })
    except Exception as e:
        print("DDGS search failed:", e)

    if not docs:
        fallback_docs = [
            {"title": "CAST AIP Documentation Home", "url": "https://doc.castsoftware.com/"},
            {"title": "CAST AIP Knowledge Base", "url": "https://doc.castsoftware.com/kb/"},
            {"title": "CAST AIP Troubleshooting Guide", "url": "https://doc.castsoftware.com/troubleshoot/"},
        ]
        docs.extend(fallback_docs[:3])

    return docs[:3]

# ---------------- AI ----------------
def ai_analyze(context: str):
    client = get_openai_client()
    if not client:
        return {"summary": "[AI analysis skipped]", "resolution": ""}

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=[
                {"role": "system",
                 "content": "You are a CAST product support expert.\n"
                            "Summarize the issue clearly and extract the concrete resolution.\n"
                            "Respond strictly in this format:\n\nSummary:\n...\n\nResolution:\n..."},
                {"role": "user", "content": context}
            ]
        )
        text = response.choices[0].message.content.strip()
        summary = re.search(r"Summary:(.*?)(Resolution:|$)", text, re.S)
        resolution = re.search(r"Resolution:(.*)", text, re.S)
        return {
            "summary": summary.group(1).strip() if summary else text,
            "resolution": resolution.group(1).strip() if resolution else ""
        }
    except Exception as e:
        return {"summary": "[AI analysis failed]", "resolution": str(e)}

# ---------------- CORE ----------------
async def analyze_ticket(ticket_id: int):
    comments = await get_ticket_comments(ticket_id)
    keywords = extract_keywords(comments)
    related_tickets = await search_related_tickets(keywords, ticket_id)
    docs = await to_thread(functools.partial(search_cast_docs, keywords))
    ai_context = f"TICKET COMMENTS:\n{comments}"
    ai_result = await to_thread(functools.partial(ai_analyze, ai_context))
    confidence = round(min(0.4 + len(related_tickets) * 0.15, 0.9), 2)
    return {
        "summary": ai_result["summary"],
        "recommended_solution": ai_result["resolution"],
        "confidence": confidence,
        "primary_ticket": {
            "id": ticket_id,
            "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{ticket_id}",
            "primary": True
        },
        "related_tickets": related_tickets,
        "related_docs": docs,
    }

# ---------------- ROUTES ----------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    try:
        return await asyncio.wait_for(analyze_ticket(req.ticket_id), timeout=40)
    except asyncio.TimeoutError:
        return {"error": "Request timed out"}
    except Exception as e:
        return {"error": str(e)}

# -------- NEW TEXT SEARCH ROUTE --------
@app.post("/ticket/search")
async def ticket_search(req: QueryRequest):
    query = req.query.strip()
    if not query:
        return {"error": "Query cannot be empty"}
    try:
        related_tickets = await search_related_tickets(query, ticket_id=0)
        docs = await to_thread(functools.partial(search_cast_docs, query))
        return {
            "query": query,
            "related_tickets": related_tickets,
            "related_docs": docs
        }
    except Exception as e:
        return {"error": str(e)}

# ---------------- PING & ENV ----------------
@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.get("/env")
def show_env():
    return {
        "ZENDESK_EMAIL_set": bool(os.getenv("ZENDESK_EMAIL")),
        "ZENDESK_API_TOKEN_set": bool(os.getenv("ZENDESK_API_TOKEN")),
        "ZENDESK_SUBDOMAIN_set": bool(os.getenv("ZENDESK_SUBDOMAIN")),
        "OPENAI_API_KEY_set": bool(os.getenv("OPENAI_API_KEY")),
    }

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
button { padding: 10px; background: #007bff; color: white; border: none; cursor:pointer; }
input { padding: 8px; width: 200px; margin-right: 10px; }
.card { background: #f9f9f9; padding: 15px; margin-top: 15px; border-radius: 5px; }
.card b { color: #333; }
a { color: #007bff; text-decoration: none; }
a:hover { text-decoration: underline; }
pre { background: #eee; padding: 10px; border-radius: 3px; }
</style>
</head>
<body>
<h1>CAST Ticket Analyzer</h1>

<h3>Analyze by Ticket ID</h3>
<input id="ticket" placeholder="Ticket ID">
<button onclick="runTicket()">Analyze Ticket</button>

<h3>Search by Text</h3>
<input id="query" placeholder="Search keywords">
<button onclick="runSearch()">Search Tickets</button>

<div id="out"></div>

<script>
async function runTicket(){
  const id = document.getElementById("ticket").value;
  const r = await fetch("/ticket/details",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({ticket_id:Number(id)})
  });
  const d = await r.json();
  showResult(d);
}

async function runSearch(){
  const q = document.getElementById("query").value;
  const r = await fetch("/ticket/search",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({query:q})
  });
  const d = await r.json();
  showResult(d);
}

function showResult(d){
  document.getElementById("out").innerHTML = `
    ${d.summary ? `<div class="card"><b>Summary</b><pre>${d.summary}</pre></div>` : ""}
    ${d.recommended_solution ? `<div class="card"><b>Recommended Solution</b><pre>${d.recommended_solution}</pre></div>` : ""}
    ${d.confidence ? `<div class="card"><b>Confidence</b>: ${d.confidence}</div>` : ""}
    ${d.query ? `<div class="card"><b>Query</b>: ${d.query}</div>` : ""}
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

# ---------------- START ----------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting CAST Ticket Analyzer on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
