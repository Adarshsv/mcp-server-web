import asyncio
import base64
import re
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx
from openai import OpenAI
from duckduckgo_search import DDGS
from asyncio import to_thread
import functools

# ---------------- ENV ----------------
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

# ---------------- ZENDESK ----------------
def zendesk_headers():
    auth = f"{os.getenv('ZENDESK_EMAIL')}/token:{os.getenv('ZENDESK_API_TOKEN')}"
    encoded = base64.b64encode(auth.encode()).decode()
    return {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}

async def get_ticket_comments(ticket_id: int):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json",
                headers=zendesk_headers(),
            )
            r.raise_for_status()
            return "\n".join(c.get("plain_body", "") for c in r.json().get("comments", []))
    except Exception as e:
        return f"[Error fetching comments: {str(e)}]"

async def search_similar_tickets(query: str):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
                headers=zendesk_headers(),
                params={"query": f"type:ticket status:solved {query}"}
            )
            r.raise_for_status()
            results = r.json().get("results", [])[:5]

        tickets = []
        resolved_context = ""
        tasks = [get_ticket_comments(t["id"]) for t in results]
        comments_list = await asyncio.gather(*tasks, return_exceptions=True)

        for idx, t in enumerate(results):
            tickets.append({
                "id": t["id"],
                "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}"
            })
            comment_text = comments_list[idx] if isinstance(comments_list[idx], str) else ""
            resolved_context += comment_text[-1500:]

        return tickets, resolved_context
    except Exception as e:
        return [], f"[Error fetching similar tickets: {str(e)}]"

def search_cast_docs(query: str):
    docs = []
    try:
        with DDGS() as ddgs:
            results = ddgs.text(f"site:doc.castsoftware.com {query}", max_results=5)
            for r in results:
                docs.append({"title": r["title"], "url": r["href"]})
    except Exception:
        pass
    return docs

def ai_analyze(context: str):
    try:
        response = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=[
                {"role": "system", "content": (
                    "You are a CAST product support expert.\n"
                    "Summarize the issue clearly and extract the concrete resolution.\n"
                    "Respond strictly in this format:\n\n"
                    "Summary:\n...\n\nResolution:\n..."
                )},
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
    except Exception as e:
        return {"summary": "[AI analysis failed]", "resolution": f"[Error: {str(e)}]"}

# ---------------- CORE LOGIC ----------------
async def analyze_ticket(ticket_id: int):
    try:
        comments = await get_ticket_comments(ticket_id)
        snippet = comments[:200]

        similar_task = search_similar_tickets(snippet)
        docs_task = to_thread(functools.partial(search_cast_docs, snippet))
        similar_tickets, resolved_context = await similar_task
        docs = await docs_task

        ai_context = f"""
TICKET COMMENTS:
{comments}

SIMILAR RESOLVED TICKETS:
{resolved_context}
"""
        ai_result = await to_thread(functools.partial(ai_analyze, ai_context))
        confidence = round(min(0.4 + len(similar_tickets) * 0.15, 0.9), 2)

        return {
            "summary": ai_result["summary"],
            "recommended_solution": ai_result["resolution"],
            "confidence": confidence,
            "related_tickets": similar_tickets,
            "related_docs": docs,
        }
    except Exception as e:
        return {"error": f"Failed to analyze ticket: {str(e)}"}

# ---------------- ROUTES ----------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    try:
        # overall timeout for slow requests (25s)
        return await asyncio.wait_for(analyze_ticket(req.ticket_id), timeout=25)
    except asyncio.TimeoutError:
        return {"error": "Request timed out. Please try again later."}
    except Exception as e:
        return {"error": str(e)}

@app.post("/search/docs")
async def search_docs(req: QueryRequest):
    try:
        return {"related_docs": await to_thread(functools.partial(search_cast_docs, req.query))}
    except Exception as e:
        return {"related_docs": [], "error": str(e)}

@app.get("/ping")
def ping():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<title>CAST Ticket Analyzer</title>
<style>
body { font-family: Arial; max-width: 900px; margin: auto; padding: 20px; }
button { padding: 10px; background: #007bff; color: white; border: none; cursor: pointer; }
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
