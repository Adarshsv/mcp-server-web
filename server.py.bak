import os
import sys
import base64
import httpx
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from duckduckgo_search import DDGS
import re

# ------------------ ENV SETUP ------------------
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "castsoftware")

# ----------------- APP DEFINITION -----------------
app = FastAPI(title="MCP Web API")

# -------- CORS --------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------- STARTUP LOGGING --------
@app.on_event("startup")
async def startup_event():
    email_loaded = bool(os.getenv("ZENDESK_EMAIL"))
    token_loaded = bool(os.getenv("ZENDESK_API_TOKEN"))
    subdomain = ZENDESK_SUBDOMAIN

    token_display = "****" + os.getenv("ZENDESK_API_TOKEN", "")[-4:] if token_loaded else None
    email_display = os.getenv("ZENDESK_EMAIL", "None") if email_loaded else "None"

    print(f"[STARTUP] Zendesk Email Loaded: {email_loaded} ({email_display})", file=sys.stderr)
    print(f"[STARTUP] Zendesk API Token Loaded: {token_loaded} ({token_display})", file=sys.stderr)
    print(f"[STARTUP] Zendesk Subdomain: {subdomain}", file=sys.stderr)

# -------- Helper: Zendesk Auth Header ----------
def zendesk_headers():
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")

    if not email or not token:
        raise HTTPException(status_code=500, detail="Zendesk credentials not set in environment")

    auth_str = f"{email}/token:{token}"
    encoded = base64.b64encode(auth_str.encode()).decode()

    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "User-Agent": "MCP-Web"
    }

# -------- Request Models --------
class QueryRequest(BaseModel):
    query: str

class TicketRequest(BaseModel):
    ticket_id: int

# -------- Helper: Summarize Comments ----------
def summarize_comments(comments):
    if not comments:
        return ""
    snippets = []
    for c in comments[:5]:
        body = c.get("plain_body") or c.get("body", "")
        if body:
            snippets.append(body.strip().replace("\n", " "))
    return " ".join(snippets)

# -------- Helper: Shorten Summary to 4-5 sentences ----------
def shorten_summary(text):
    sentences = re.split(r'\. |\n', text)
    shortened = ". ".join(sentences[:5])
    if len(sentences) > 5:
        shortened += " ..."
    return shortened

# -------- Helper: Highlight Keywords ----------
def highlight_keywords(text, keywords):
    for kw in keywords:
        if kw.strip():
            pattern = re.compile(re.escape(kw), re.IGNORECASE)
            text = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", text)
    return text

# -------- Shared Helper: Build Summary --------
async def generate_summary(query=None, ticket_ids=None):
    related_tickets = []
    related_docs = []
    summarized_comments = ""

    async with httpx.AsyncClient(timeout=30) as client:
        headers = zendesk_headers()

        # -------- Fetch Related Tickets --------
        if ticket_ids:
            ticket_promises = []
            for tid in ticket_ids:
                ticket_promises.append(client.get(
                    f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{tid}/comments.json",
                    headers=headers
                ))
                related_tickets.append({
                    "id": tid,
                    "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{tid}"
                })
            comments_responses = await asyncio.gather(*ticket_promises)
            for c_resp in comments_responses:
                comments = c_resp.json().get("comments", [])
                summarized_comments += summarize_comments(comments) + " "
        elif query:
            try:
                resp = await client.get(
                    f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
                    headers=headers,
                    params={"query": f"type:ticket {query}"}
                )
                resp.raise_for_status()
                tickets = resp.json().get("results", [])
                for t in tickets[:5]:
                    related_tickets.append({
                        "id": t["id"],
                        "subject": t["subject"],
                        "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}"
                    })
                comments_responses = await asyncio.gather(*[
                    client.get(
                        f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{t['id']}/comments.json",
                        headers=headers
                    ) for t in tickets[:5]
                ])
                for c_resp in comments_responses:
                    comments = c_resp.json().get("comments", [])
                    summarized_comments += summarize_comments(comments) + " "
            except Exception:
                pass

        # -------- Fetch Related Docs --------
        if query:
            try:
                with DDGS() as ddgs:
                    docs = ddgs.text(f"site:doc.castsoftware.com {query}", max_results=3)
                    for d in docs:
                        related_docs.append({"title": d["title"], "url": d["href"]})
            except Exception:
                pass

        # -------- Generate Recommended Solution --------
        if related_tickets or related_docs:
            recommended_solution = (
                "Based on similar tickets and documentation, "
                "apply recommended updates, known workarounds, "
                "or adjust configuration as per CAST guidelines."
            )
        else:
            recommended_solution = (
                "No direct reference found. Investigate ticket comments "
                "and CAST documentation for potential solution."
            )

        # -------- Build Summary Text --------
        shortened_summary = shorten_summary(summarized_comments.strip() or "No ticket comments found.")
        highlighted_summary = highlight_keywords(shortened_summary, query.split() if query else [])

        summary_lines = [
            f"Observed Behavior: {highlighted_summary}",
            f"Similar Issues: {len(related_tickets)} related tickets found.",
            f"Documentation References: {len(related_docs)} docs found.",
            f"Suggested Resolution: {recommended_solution}"
        ]

        summary_text = "\n\n".join(summary_lines)
        confidence = round(min(0.3 + len(related_tickets) * 0.15, 0.9), 2)

        return {
            "summary": summary_text,
            "confidence": confidence,
            "related_tickets": related_tickets,
            "related_docs": related_docs,
            "recommended_solution": recommended_solution
        }

# -------- ROUTES --------
@app.get("/debug/env")
def debug_env():
    return {
        "email": bool(os.getenv("ZENDESK_EMAIL")),
        "token": bool(os.getenv("ZENDESK_API_TOKEN")),
        "subdomain": ZENDESK_SUBDOMAIN,
    }

@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    return await generate_summary(ticket_ids=[req.ticket_id])

@app.post("/search/all")
async def search_all(req: QueryRequest):
    return await generate_summary(query=req.query)

# -------- Web UI --------
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MCP Support Dashboard</title>
        <style>
            body { font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px; max-width: 1000px; margin: auto; }
            h1 { text-align: center; }
            input, button { padding: 10px; margin: 5px 0; width: 100%; max-width: 400px; border-radius: 5px; border: 1px solid #ccc; }
            button { cursor: pointer; background: #007bff; color: #fff; border: none; }
            button:hover { background: #0056b3; }
            .result { margin-top: 20px; }
            .card { background: #fff; border-radius: 8px; padding: 15px; margin-bottom: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
            .card a { text-decoration: none; color: #007bff; }
            .section-title { font-weight: bold; margin-bottom: 10px; }
            pre { white-space: pre-wrap; word-wrap: break-word; }
            mark { background-color: #ffff00; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>CAST Support Dashboard</h1>

        <label>Ticket ID (optional):</label>
        <input type="number" id="ticket_id" placeholder="Enter ticket ID">
        <label>Or Search Query:</label>
        <input type="text" id="query" placeholder="Enter keywords to search">
        <button onclick="submitQuery()">Submit</button>

        <div class="result" id="result"></div>

        <script>
            async function submitQuery() {
                const ticketId = document.getElementById('ticket_id').value;
                const query = document.getElementById('query').value;
                const resultDiv = document.getElementById('result');
                resultDiv.innerHTML = "<p>Loading...</p>";

                let url, body;
                if (ticketId) {
                    url = '/ticket/details';
                    body = JSON.stringify({ ticket_id: parseInt(ticketId) });
                } else if (query) {
                    url = '/search/all';
                    body = JSON.stringify({ query: query });
                } else {
                    resultDiv.innerHTML = "<p>Please enter ticket ID or search query!</p>";
                    return;
                }

                try {
                    const response = await fetch(url, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: body
                    });
                    if (!response.ok) {
                        resultDiv.innerHTML = "<p>Error: " + response.statusText + "</p>";
                        return;
                    }
                    const data = await response.json();

                    let html = `<div class="card"><div class="section-title">Summary</div><pre>${data.summary}</pre></div>`;

                    // Confidence color-coded
                    let confidenceColor = data.confidence > 0.7 ? "green" : data.confidence > 0.5 ? "orange" : "red";
                    html += `<div class="card"><div class="section-title">Confidence</div><pre style="color:${confidenceColor}">${data.confidence}</pre></div>`;

                    html += `<div class="card"><div class="section-title">Related Tickets</div>`;
                    if (data.related_tickets.length > 0) {
                        data.related_tickets.forEach(t => {
                            const title = t.subject || "Ticket " + t.id;
                            html += `<p><a href="${t.url}" target="_blank">${title}</a></p>`;
                        });
                    } else {
                        html += "<p>No related tickets found.</p>";
                    }
                    html += "</div>";

                    html += `<div class="card"><div class="section-title">Related Documentation</div>`;
                    if (data.related_docs.length > 0) {
                        data.related_docs.forEach(d => {
                            html += `<p><a href="${d.url}" target="_blank">${d.title}</a></p>`;
                        });
                    } else {
                        html += "<p>No related documentation found.</p>";
                    }
                    html += "</div>";

                    html += `<div class="card"><div class="section-title">Recommended Solution</div><pre>${data.recommended_solution}</pre></div>`;

                    resultDiv.innerHTML = html;
                } catch (err) {
                    resultDiv.innerHTML = "<p>Error: " + err + "</p>";
                }
            }
        </script>
    </body>
    </html>
    """
