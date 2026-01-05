import os
import sys
import base64
import re
import httpx
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

# ------------------ CONFIG ------------------
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "castsoftware")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

# ------------------ APP ------------------
app = FastAPI(title="CAST Ticket Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ OPENAI ------------------
openai_client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

# ------------------ AUTH ------------------
def zendesk_headers():
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")

    if not email or not token:
        raise HTTPException(500, "Zendesk credentials missing")

    auth = base64.b64encode(
        f"{email}/token:{token}".encode()
    ).decode()

    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
        "User-Agent": "CAST-Ticket-Analyzer"
    }

# ------------------ MODELS ------------------
class TicketRequest(BaseModel):
    ticket_id: int

# ------------------ TEXT CLEANING ------------------
def clean_text(text: str) -> str:
    text = re.sub(r"\[cid:.*?\]", "", text)
    text = re.sub(r"http\S+", "", text)
    text = re.sub(r"Regards,.*", "", text, flags=re.I | re.S)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()

# ------------------ COMMENT PROCESSING ------------------
def split_comments(comments):
    user_msgs, agent_msgs = [], []

    for c in comments:
        body = clean_text(c.get("plain_body") or c.get("body", ""))
        if not body:
            continue

        if c.get("public"):
            if c.get("author_id"):
                user_msgs.append(body)
            else:
                agent_msgs.append(body)

    return user_msgs, agent_msgs

# ------------------ OPENAI SUMMARY ------------------
def openai_summarize(user_text, agent_text):
    if not openai_client:
        return None, None

    prompt = f"""
You are a CAST Software support engineer.

Ticket description:
{user_text}

Agent responses:
{agent_text}

Tasks:
1. Summarize the observed issue in 3–4 clear sentences.
2. Extract or infer the most likely resolution.
3. Be precise, technical, and concise.
"""

    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )

        text = resp.choices[0].message.content.strip()
        parts = text.split("Resolution:")

        summary = parts[0].strip()
        resolution = parts[1].strip() if len(parts) > 1 else None

        return summary, resolution

    except Exception as e:
        print("[OpenAI error]", e, file=sys.stderr)
        return None, None

# ------------------ ANALYSIS ------------------
async def analyze_ticket(ticket_id: int):
    headers = zendesk_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json",
            headers=headers
        )
        resp.raise_for_status()

    comments = resp.json().get("comments", [])
    user_msgs, agent_msgs = split_comments(comments)

    raw_user = " ".join(user_msgs[:5])
    raw_agent = " ".join(agent_msgs[:5])

    ai_summary, ai_resolution = openai_summarize(raw_user, raw_agent)

    summary = ai_summary or (
        raw_user[:500]
        if raw_user
        else "Issue reported but no clear reproduction steps found."
    )

    resolution = ai_resolution or (
        raw_agent[:400]
        if raw_agent
        else "Collect logs and escalate for further investigation."
    )

    confidence = round(
        min(0.4 + (0.3 if ai_summary else 0.1), 0.9), 2
    )

    return {
        "summary": summary,
        "confidence": confidence,
        "related_tickets": [{
            "id": ticket_id,
            "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{ticket_id}"
        }],
        "related_docs": [],
        "recommended_solution": resolution
    }

# ------------------ API ------------------
@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    return await analyze_ticket(req.ticket_id)

# ------------------ UI ------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<title>CAST Ticket Analyzer</title>
<style>
body { font-family: Arial; background:#f4f4f4; padding:40px; max-width:900px; margin:auto; }
input, button { padding:10px; width:100%; margin:8px 0; }
button { background:#007bff; color:white; border:none; cursor:pointer; }
.card { background:white; padding:15px; margin-top:15px; border-radius:6px; }
</style>
</head>
<body>
<h1>CAST Ticket Analyzer</h1>

<input id="ticket" placeholder="Ticket ID">
<button onclick="run()">Analyze</button>

<div id="out"></div>

<script>
async function run() {
  const id = document.getElementById("ticket").value;
  const res = await fetch("/ticket/details", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ticket_id: parseInt(id)})
  });
  const data = await res.json();

  document.getElementById("out").innerHTML = `
    <div class="card"><b>Summary</b><pre>${data.summary}</pre></div>
    <div class="card"><b>Confidence</b>: ${data.confidence}</div>
    <div class="card"><b>Recommended Solution</b><pre>${data.recommended_solution}</pre></div>
  `;
}
</script>
</body>
</html>
"""
