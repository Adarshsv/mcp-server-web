import asyncio
import base64
import os
import re
import functools
from typing import Optional, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx
from duckduckgo_search import DDGS
from openai import OpenAI

# =========================================================
# ENV
# =========================================================

REQUIRED_ENVS = [
    "ZENDESK_EMAIL",
    "ZENDESK_API_TOKEN",
    "ZENDESK_SUBDOMAIN",
]

for e in REQUIRED_ENVS:
    if not os.getenv(e):
        print(f"[WARN] {e} missing")

ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL", "")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN", "")
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# =========================================================
# APP
# =========================================================

app = FastAPI(title="CAST Ticket Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

async_client = httpx.AsyncClient(timeout=12)

@app.on_event("shutdown")
async def shutdown():
    await async_client.aclose()

# =========================================================
# MODELS
# =========================================================

class AnalyzeRequest(BaseModel):
    query: str  # ticket id OR free text

# =========================================================
# UTILITIES
# =========================================================

async def safe_call(fn, timeout=6, fallback=None):
    try:
        return await asyncio.wait_for(fn, timeout)
    except Exception as e:
        print("[SAFE_CALL]", e)
        return fallback

def extract_keywords(text: str, max_words=6) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text)
    blacklist = {"error", "issue", "problem", "unable", "failed"}
    keywords = [w for w in words if w.lower() not in blacklist]
    return " ".join(keywords[:max_words]) or text.strip()

# =========================================================
# ZENDESK
# =========================================================

def zendesk_headers():
    auth = f"{ZENDESK_EMAIL}/token:{ZENDESK_API_TOKEN}"
    encoded = base64.b64encode(auth.encode()).decode()
    return {"Authorization": f"Basic {encoded}"}

async def get_ticket_comments(ticket_id: int) -> str:
    r = await async_client.get(
        f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json",
        headers=zendesk_headers(),
    )
    r.raise_for_status()
    return "\n".join(c["plain_body"] for c in r.json().get("comments", []))

async def search_solved_tickets(query: str) -> List[dict]:
    q = f"type:ticket status:solved {query}"
    r = await async_client.get(
        f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
        headers=zendesk_headers(),
        params={"query": q},
    )
    r.raise_for_status()

    tickets = []
    for t in r.json().get("results", [])[:3]:
        tickets.append({
            "id": t["id"],
            "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}",
            "comment": t.get("description", "")[:300]
        })
    return tickets

# =========================================================
# DOC SEARCH (FAST + FALLBACK)
# =========================================================

def search_cast_docs_fast(query: str) -> List[dict]:
    """
    CURRENT (FAST):
    DuckDuckGo keyword search

    FUTURE (REAL SOLUTION):
    - Crawl doc.castsoftware.com
    - Build keyword index
    - Cache results (Redis / SQLite)
    """
    docs = []
    ddg_query = f"CAST AIP {query} site:doc.castsoftware.com"

    try:
        with DDGS() as ddgs:
            for r in ddgs.text(ddg_query, max_results=5):
                docs.append({
                    "title": r.get("title", ""),
                    "url": r.get("href"),
                    "comment": "Matched CAST documentation"
                })
    except Exception as e:
        print("[DOC SEARCH]", e)

    if not docs:
        docs.append({
            "title": "CAST AIP Documentation Home",
            "url": "https://doc.castsoftware.com/",
            "comment": "General CAST documentation"
        })

    return docs[:3]

# =========================================================
# AI (OPTIONAL, NON-BLOCKING)
# =========================================================

def ai_summarize(text: str):
    if not OPENAI_API_KEY:
        return {"summary": "", "confidence": 0.4}

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=[
                {"role": "system", "content": "Summarize issue briefly."},
                {"role": "user", "content": text},
            ],
        )
        return {
            "summary": res.choices[0].message.content.strip(),
            "confidence": 0.85
        }
    except Exception as e:
        print("[AI]", e)
        return {"summary": "", "confidence": 0.4}

# =========================================================
# CORE ANALYSIS
# =========================================================

@app.post("/ticket/analyze")
async def analyze(req: AnalyzeRequest):
    raw = req.query.strip()

    # Detect ticket id
    ticket_id = int(raw) if raw.isdigit() else None
    search_text = raw

    if ticket_id:
        comments = await safe_call(
            get_ticket_comments(ticket_id),
            fallback=""
        )
        search_text = extract_keywords(comments)

    # ---- PARALLEL EXECUTION ----
    doc_task = asyncio.create_task(
        safe_call(
            asyncio.to_thread(search_cast_docs_fast, search_text),
            timeout=4,
            fallback=[]
        )
    )

    ticket_task = asyncio.create_task(
        safe_call(
            search_solved_tickets(search_text),
            timeout=6,
            fallback=[]
        )
    )

    ai_task = asyncio.create_task(
        safe_call(
            asyncio.to_thread(ai_summarize, search_text),
            timeout=8,
            fallback={"summary": "", "confidence": 0.4}
        )
    )

    docs = await doc_task
    tickets = await ticket_task
    ai = await ai_task

    return {
        "query": raw,
        "summary": ai.get("summary", ""),
        "confidence": ai.get("confidence", 0.4),
        "related_tickets": tickets,
        "related_docs": docs,
    }

# =========================================================
# HEALTH
# =========================================================

@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.get("/env")
def env():
    return {
        "ZENDESK": bool(ZENDESK_API_TOKEN),
        "OPENAI": bool(OPENAI_API_KEY)
    }

# =========================================================
# UI (SIMPLE, FAST)
# =========================================================

@app.get("/", response_class=HTMLResponse)
def ui():
    return """
<!DOCTYPE html>
<html>
<head>
<title>CAST Ticket Analyzer</title>
<style>
body { font-family: Arial; margin: 40px; }
input { width: 300px; padding: 8px; }
button { padding: 8px 14px; }
pre { background: #f4f4f4; padding: 10px; }
</style>
</head>
<body>
<h2>CAST Ticket Analyzer</h2>
<input id="q" placeholder="Ticket ID or keywords"/>
<button onclick="run()">Analyze</button>
<pre id="out"></pre>

<script>
async function run(){
  out.textContent = "Loading...";
  const q = document.getElementById("q").value;
  const r = await fetch("/ticket/analyze", {
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({query:q})
  });
  out.textContent = JSON.stringify(await r.json(), null, 2);
}
</script>
</body>
</html>
"""

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        workers=2,
    )
