import os
import sys
import httpx
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

ZENDESK_SUBDOMAIN = "castsoftware"
ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN")

if ZENDESK_EMAIL and ZENDESK_API_TOKEN:
    print("✅ Zendesk API credentials loaded", file=sys.stderr)
else:
    print("❌ Missing Zendesk API credentials", file=sys.stderr)

def zendesk_auth():
    return (f"{ZENDESK_EMAIL}/token", ZENDESK_API_TOKEN)

@app.post("/ticket/details")
async def get_ticket_details(payload: dict):
    ticket_id = payload.get("ticket_id")
    if not ticket_id:
        return {"error": "ticket_id required"}

    if not ZENDESK_EMAIL or not ZENDESK_API_TOKEN:
        return {"error": "Zendesk API credentials not configured"}

    async with httpx.AsyncClient(auth=zendesk_auth()) as client:
        ticket_url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}.json"
        comments_url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json"

        t_resp = await client.get(ticket_url)
        if t_resp.status_code == 404:
            return {"error": "Ticket not found"}
        if t_resp.status_code == 401:
            return {"error": "Zendesk authentication failed"}

        ticket = t_resp.json()["ticket"]

        c_resp = await client.get(comments_url)
        comments = c_resp.json()["comments"]

        history = [
            {
                "author_id": c["author_id"],
                "comment": c.get("plain_body", "")
            }
            for c in comments
        ]

        return {
            "id": ticket["id"],
            "subject": ticket["subject"],
            "status": ticket["status"],
            "description": ticket["description"],
            "history": history
        }
