import os
import sys
import httpx
from mcp.server.fastmcp import FastMCP
from duckduckgo_search import DDGS
from dotenv import load_dotenv

# --- AUTHENTICATION SETUP ---
# 1. Calculate the absolute path to the .env file
current_dir = os.path.dirname(os.path.abspath(__file__))
env_file_path = os.path.join(current_dir, '.env')

# 2. Load it explicitly
load_dotenv(env_file_path)

# 3. Get the Cookie
ZENDESK_COOKIE = os.getenv("ZENDESK_COOKIE")

# Debug Print to logs (helps troubleshooting)
print(f"Loading config from: {env_file_path}", file=sys.stderr)
if ZENDESK_COOKIE:
    print("✅ Cookie loaded successfully", file=sys.stderr)
else:
    print("❌ Cookie NOT found in environment", file=sys.stderr)
# ---------------------------

mcp = FastMCP("CAST_Zendesk_Helper")
ZENDESK_SUBDOMAIN = "castsoftware"

@mcp.tool()
async def search_cast_documentation(query: str) -> str:
    """Searches official CAST documentation."""
    try:
        with DDGS() as ddgs:
            results = ddgs.text(f"site:doc.castsoftware.com {query}", max_results=5)
            if not results: return "No documentation found."
            return "\n".join([f"Title: {r['title']}\nLink: {r['href']}\nSnippet: {r['body']}\n---" for r in results])
    except Exception as e: return f"Search Error: {e}"

@mcp.tool()
async def search_zendesk_tickets(query: str) -> str:
    """Searches for a list of tickets."""
    if not ZENDESK_COOKIE: return "Error: .env file not loaded. Cookie missing."
    
    url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json"
    headers = {"Cookie": ZENDESK_COOKIE, "User-Agent": "MCP-Bot"}
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params={"query": f"type:ticket {query}"}, headers=headers)
        if resp.status_code == 401: return "Error: Cookie Expired."
        results = resp.json().get("results", [])
        return "\n".join([f"ID: {t['id']} | {t['subject']}" for t in results[:5]]) if results else "No tickets."

@mcp.tool()
async def get_ticket_details(ticket_id: int) -> str:
    """Fetches details of a specific ticket."""
    if not ZENDESK_COOKIE: return "Error: .env file not loaded. Cookie missing."

    headers = {"Cookie": ZENDESK_COOKIE, "User-Agent": "MCP-Bot"}
    client = httpx.AsyncClient()
    
    try:
        # Get Ticket
        t_resp = await client.get(f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}.json", headers=headers)
        if t_resp.status_code == 401: return "Error: Cookie Expired. Update .env file."
        if t_resp.status_code == 404: return "Ticket not found."
        
        ticket = t_resp.json().get("ticket", {})
        
        # Get Comments
        c_resp = await client.get(f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json", headers=headers)
        comments = c_resp.json().get("comments", [])
        
        conversation = [f"User {c['author_id']}: {c.get('plain_body','')}" for c in comments]
        
        return (f"TICKET #{ticket['id']}: {ticket['subject']}\n"
                f"STATUS: {ticket['status']}\n"
                f"DESC: {ticket['description']}\n\n"
                "HISTORY:\n" + "\n---\n".join(conversation))
    finally:
        await client.aclose()

if __name__ == "__main__":
    mcp.run()