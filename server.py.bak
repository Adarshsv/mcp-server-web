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
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)

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

# ---------------- HELPERS ----------------
def extract_keywords(text: str, max_words=8):
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text)
    blacklist = {"error", "issue", "problem", "unable", "failed"}
    keywords = [w for w in words if w.lower() not in blacklist]
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
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json",
            headers=zendesk_headers(),
        )
        r.raise_for_status()
        return "\n".join(
            c.get("plain_body", "")
            for c in r.json().get("comments", [])
        )

async def search_related_tickets(query: str, ticket_id: int):
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
            headers=zendesk_headers(),
            params={"query": f"type:ticket status:solved {query}"}
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

    return related

# ---------------- DOC SEARCH ----------------
def search_cast_docs(query: str):
    docs = []
    try:
        with DDGS() as ddgs:
            results = ddgs.text(
                f"CAST AIP {query} site:doc.castsoftware.com",
                max_results=5
            )
            for r in results:
                docs.append({
                    "title": r["title"],
                    "url": r["href"]
                })
    except Exception:
        pass

    return docs[:3]

# ---------------- AI ----------------
def ai_analyze(context: str):
    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a CAST product support expert.\n"
                        "Summarize the issue clearly and extract the concrete resolution.\n"
                        "Respond strictly in this format:\n\n"
                        "Summary:\n...\n\nResolution:\n..."
                    )
                },
                {"role": "user", "content": context},
            ],
        )

        text = response.choices[0].message.content.strip()
        summary = re.search(r"Summary:(.*?)(Resolution:|$)", text, re.S)
        resolution = re.search(r"Resolution:(.*)", text, re.S)

        return {
            "summary": summary.group(1).strip() if summary else text,
            "resolution": resolution.group(1).strip() if resolution else ""
        }

    except Exception as e:
        return {
            "summary": "[AI analysis failed]",
            "resolution": str(e)
        }

# ---------------- CORE ----------------
async def analyze_ticket(ticket_id: int):
    comments = await get_ticket_comments(ticket_id)
    keywords = extract_keywords(comments)

    related_tickets = await search_related_tickets(keywords, ticket_id)
    docs = await to_thread(functools.partial(search_cast_docs, keywords))

    ai_context = f"""
TICKET COMMENTS:
{comments}
"""

    ai_result = await to_thread(
        functools.partial(ai_analyze, ai_context)
    )

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
        return await asyncio.wait_for(
            analyze_ticket(req.ticket_id),
            timeout=25
        )
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

# ---------------- START ----------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting CAST Ticket Analyzer on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
