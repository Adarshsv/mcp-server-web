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

@app.on_event("shutdown")
async def shutdown_event():
    await async_client.aclose()

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
    return {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}

async def get_ticket_comments(ticket_id: int):
    r = await async_client.get(
        f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json",
        headers=zendesk_headers(),
    )
    r.raise_for_status()
    return "\n".join(c.get("plain_body", "") for c in r.json().get("comments", []))

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
        related.append({"id": t["id"], "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}"})
        if len(related) == 3:
            break

    if not related:
        print("No related tickets found. Using last 3 solved tickets as fallback.")
        r = await async_client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets.json",
            headers=zendesk_headers(),
            params={"status": "solved", "sort_by": "updated_at", "sort_order": "desc"}
        )
        r.raise_for_status()
        for t in r.json().get("tickets", [])[:3]:
            if t["id"] != ticket_id:
                related.append({"id": t["id"], "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}"})
    return related

# ---------------- DOC SEARCH ----------------
def search_cast_docs(query: str):
    docs = []
    query = query.strip() or "CAST AIP"
    ddg_query = f"CAST AIP {query} site:doc.castsoftware.com"
    print("DDG search query:", ddg_query)
    try:
        with DDGS() as ddgs:
            results = ddgs.text(ddg_query, max_results=5)
            for r in results:
                docs.append({"title": r.get("title", "Untitled"), "url": r.get("href")})
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
        return {"summary": summary.group(1).strip() if summary else text,
                "resolution": resolution.group(1).strip() if resolution else ""}
    except Exception as e:
        return {"summary": "[AI analysis failed]", "resolution": str(e)}

# ---------------- CORE ----------------
async def analyze_ticket(ticket_id: int):
    print(f"Analyzing ticket {ticket_id}...")
    comments = await get_ticket_comments(ticket_id)
    keywords = extract_keywords(comments)
    print("Extracted keywords:", keywords)

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
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CAST Ticket Analyzer</title>
<style>
body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; margin:0; padding:0; }
.container { max-width:1000px; margin:40px auto; padding:20px; }
h1 { text-align:center; color:#333; }
.controls { display:flex; justify-content:center; gap:10px; margin-bottom:20px; }
input { padding:10px; width:150px; border-radius:5px; border:1px solid #ccc; }
button { padding:10px 20px; background:#007bff; color:white; border:none; border-radius:5px; cursor:pointer; transition:.3s; }
button:hover { background:#0056b3; }
.card { background:white; padding:20px; margin-bottom:15px; border-radius:8px; box-shadow:0 3px 8px rgba(0,0,0,0.1); }
.card h2 { margin-top:0; color:#007bff; font-size:18px; }
.card pre { background:#f6f8fa; padding:10px; border-radius:5px; overflow-x:auto; }
.resolution { background:#e6f7ff; border-left:5px solid #1890ff; padding:10px; font-weight:bold; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:10px; }
.grid a { display:block; padding:10px; background:#f9f9f9; border-radius:5px; text-align:center; transition:.2s; }
.grid a:hover { background:#007bff; color:white; }
.spinner { border:6px solid #f3f3f3; border-top:6px solid #007bff; border-radius:50%; width:40px; height:40px; animation:spin 1s linear infinite; margin:auto; margin-top:20px; }
@keyframes spin { 0% { transform:rotate(0deg); } 100% { transform:rotate(360deg); } }
#progress { text-align:center; font-style:italic; color:#555; margin-bottom:15px; }
</style>
</head>
<body>
<div class="container">
<h1>CAST Ticket Analyzer</h1>
<div class="controls">
<input id="ticket" placeholder="Ticket ID" />
<button onclick="run()">Analyze</button>
</div>
<div id="progress"></div>
<div id="out"></div>
<script>
async function run(){
    const id=document.getElementById("ticket").value;
    if(!id){ alert('Enter a ticket ID'); return; }
    const outDiv=document.getElementById("out");
    const progressDiv=document.getElementById("progress");
    outDiv.innerHTML="";
    progressDiv.innerHTML="<div class='spinner'></div><p>Fetching ticket data...</p>";
    try{
        const r=await fetch("/ticket/details",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ticket_id:Number(id)})});
        const d=await r.json();
        if(d.error){ progressDiv.innerHTML=""; outDiv.innerHTML=`<p style="color:red;">Error: ${d.error}</p>`; return; }
        progressDiv.innerHTML="<p>Analysis complete!</p>";
        outDiv.innerHTML=`
            <div class="card"><h2>Summary</h2><pre>${d.summary||'[No summary]'}</pre></div>
            <div class="card"><h2>Confidence</h2><p>${d.confidence||0}</p></div>
            <div class="card resolution"><h2>Recommended Solution</h2><pre>${d.recommended_solution||'[No solution]'}</pre></div>
            <div class="card"><h2>Related Tickets</h2><div class="grid">${(d.related_tickets||[]).map(t=>`<a href="${t.url}" target="_blank">${t.id}</a>`).join("")||"<p>No related tickets</p>"}</div></div>
            <div class="card"><h2>Documentation</h2><div class="grid">${(d.related_docs||[]).map(doc=>`<a href="${doc.url}" target="_blank">${doc.title}</a>`).join("")||"<p>No documentation found</p>"}</div></div>
        `;
    }catch(e){ progressDiv.innerHTML=""; outDiv.innerHTML=`<p style="color:red;">Fetch error: ${e}</p>`; }
}
</script>
</div>
</body>
</html>
"""

# ---------------- START ----------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting CAST Ticket Analyzer on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
