import os
import sys
import base64
import httpx
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from duckduckgo_search import DDGS

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
    for c in comments[:5]:  # limit to top 5 comments
        body = c.get("plain_body") or c.get("body", "")
        if body:
            snippets.append(body.strip().replace("\n", " "))
    return " ".join(snippets)

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
            # Search by keyword
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
                # summarize comments
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
        summary_lines = []
        if query:
            summary_lines.append(f"Search Query: {query}")
        summary_lines.append(f"Observed Behavior: {summarized_comments.strip() or 'No ticket comments found.'}")
        summary_lines.append(f"Similar Issues: {len(related_tickets)} related tickets found.")
        summary_lines.append(f"Documentation References: {len(related_docs)} docs found.")
        summary_lines.append(f"Suggested Resolution: {recommended_solution}")

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

@app.get("/")
def health():
    return {"status": "ok"}
