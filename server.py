import os
import re
import asyncio
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from openai import OpenAI
from duckduckgo_search import DDGS

# ================== APP SETUP ==================

app = FastAPI(title="CAST Ticket Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ================== MODELS ==================

class AnalyzeRequest(BaseModel):
    query: str


# ================== HELPERS ==================

GENERIC_DOCS = [
    {"title": "CAST AIP Documentation Home", "url": "https://doc.castsoftware.com/", "comment": "General CAST documentation"},
]

def is_ticket_id(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4,7}", value.strip()))

# ================== DOC SEARCH (FIRST) ==================

async def search_docs(keyword: str) -> List[Dict[str, str]]:
    docs = []
    with DDGS() as ddgs:
        for r in ddgs.text(
            f"{keyword} site:doc.castsoftware.com",
            max_results=5
        ):
            docs.append({
                "title": r.get("title"),
                "url": r.get("href"),
                "comment": f"Mentions '{keyword}'"
            })
    return docs

# ================== TICKET SEARCH (SECOND) ==================

async def search_solved_tickets(keyword: str) -> List[Dict[str, Any]]:
    """
    Stub for Zendesk solved ticket search.
    Replace this with real Zendesk API search.
    """
    return []

# ================== AI SUMMARY (LAST) ==================

async def ai_summary(text: str) -> Dict[str, Any]:
    resp = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a CAST AIP support engineer."},
            {"role": "user", "content": text}
        ]
    )
    return {
        "summary": resp.choices[0].message.content.strip(),
        "confidence": 0.85
    }

# ================== CORE ANALYSIS ==================

async def analyze_query(query: str) -> Dict[str, Any]:
    result = {
        "query": query,
        "summary": "",
        "confidence": None,
        "related_tickets": [],
        "related_docs": []
    }

    # 1️⃣ DOCS FIRST
    docs = await search_docs(query)
    result["related_docs"] = docs if docs else GENERIC_DOCS

    # 2️⃣ TICKETS
    result["related_tickets"] = await search_solved_tickets(query)

    # 3️⃣ AI SUMMARY
    ai = await ai_summary(query)
    result.update(ai)

    return result

# ================== API ==================

@app.post("/ticket/analyze")
async def analyze(req: AnalyzeRequest):
    if not req.query:
        raise HTTPException(status_code=400, detail="Query required")
    return await analyze_query(req.query.strip())

@app.post("/ticket/search")
async def ticket_search(req: AnalyzeRequest):
    return await analyze(req)

@app.post("/ticket/details")
async def ticket_details(req: AnalyzeRequest):
    return await analyze(req)

# ================== WEB UI ==================

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
    h3 { margin-top: 30px; }
    .item { margin-bottom: 8px; }
  </style>
</head>
<body>

<h2>CAST Ticket Analyzer</h2>

<input id="query" placeholder="Ticket ID or keywords"/>
<button onclick="analyze()">Analyze</button>

<h3>Summary</h3>
<div id="summary">—</div>
<div id="confidence"></div>

<h3>Related Tickets</h3>
<div id="tickets">—</div>

<h3>Documentation</h3>
<div id="docs">—</div>

<script>
async function analyze() {
  const q = document.getElementById("query").value;
  document.getElementById("summary").innerText = "Loading...";
  document.getElementById("tickets").innerHTML = "";
  document.getElementById("docs").innerHTML = "";

  const res = await fetch("/ticket/analyze", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({query:q})
  });

  const data = await res.json();

  document.getElementById("summary").innerText = data.summary || "—";
  document.getElementById("confidence").innerText =
    data.confidence ? "Confidence: " + data.confidence : "";

  const t = document.getElementById("tickets");
  if (data.related_tickets.length === 0) t.innerText = "No related tickets";
  data.related_tickets.forEach(x => {
    t.innerHTML += `<div class="item">${x.id || ""} ${x.comment || ""}</div>`;
  });

  const d = document.getElementById("docs");
  data.related_docs.forEach(x => {
    d.innerHTML += `<div class="item">
      <a href="${x.url}" target="_blank">${x.title}</a> – ${x.comment || ""}
    </div>`;
  });
}
</script>

</body>
</html>
"""
