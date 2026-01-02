import os
import sys
import httpx
from duckduckgo_search import DDGS
from dotenv import load_dotenv

# --- ENV LOADING ---
current_dir = os.path.dirname(os.path.abspath(__file__))
env_file_path = os.path.join(current_dir, '.env')
load_dotenv(env_file_path)

ZENDESK_COOKIE = os.getenv("ZENDESK_COOKIE")
ZENDESK_SUBDOMAIN = "castsoftware"

print(f"Loading config from: {env_file_path}", file=sys.stderr)
print("✅ Cookie loaded" if ZENDESK_COOKIE else "❌ Cookie missing", file=sys.stderr)

# ---------------- MCP LOGIC ---------------- #

async def search_cast_documentation(query: str) -> str:
    try:
        with DDGS() as ddgs:
            results = ddgs.text(
                f"site:doc.castsoftware.com {query}",
                max_results=5
            )
            if not results:
                return "No documentation found."

            return "\n".join(
                f"Title: {r['title']}\nLink: {r['href']}\nSnippet: {r['body']}\n---"
                for r in results
            )
    except Exception as e:
        return f"Search Error: {e}"


async def search_zendesk_tickets(query: str) -> str:
    if not ZENDESK_COOKIE:
        return "Error: Zendesk cookie missing."

    url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json"
    headers = {"Cookie": ZENDESK_COOKIE, "User-Agent": "MCP-Bot"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url,
            params={"query": f"type:ticket {query}"},
            headers=headers
        )

        if resp.status_code == 401:
            return "Error: Cookie expired."

        results = resp.json().get("results", [])
        return (
            "\n".join(f"ID: {t['id']} | {t['subject']}" for t in results[:5])
            if results else "No tickets found."
        )


async def get_ticket_details(ticket_id: int) -> str:
    if not ZENDESK_COOKIE:
        return "Error: Zendesk cookie missing."

    headers = {"Cookie": ZENDESK_COOKIE, "User-Agent": "MCP-Bot"}

    async with httpx.AsyncClient() as client:
        t_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}.json",
            headers=headers
        )

        if t_resp.status_code == 401:
            return "Error: Cookie expired."
        if t_resp.status_code == 404:
            return "Ticket not found."

        ticket = t_resp.json().get("ticket", {})

        c_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json",
            headers=headers
        )

        comments = c_resp.json().get("comments", [])
        conversation = [
            f"User {c['author_id']}: {c.get('plain_body', '')}"
            for c in comments
        ]

        return (
            f"TICKET #{ticket['id']}: {ticket['subject']}\n"
            f"STATUS: {ticket['status']}\n"
            f"DESC: {ticket['description']}\n\n"
            "HISTORY:\n" + "\n---\n".join(conversation)
        )
