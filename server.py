from fastapi import FastAPI
from pydantic import BaseModel
import mcp_logic

app = FastAPI(title="CAST Zendesk MCP Web")

class Query(BaseModel):
    query: str

class Ticket(BaseModel):
    ticket_id: int

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/search/docs")
async def search_docs(q: Query):
    return {"result": await mcp_logic.search_cast_documentation(q.query)}

@app.post("/search/tickets")
async def search_tickets(q: Query):
    return {"result": await mcp_logic.search_zendesk_tickets(q.query)}

@app.post("/ticket/details")
async def ticket_details(t: Ticket):
    return {"result": await mcp_logic.get_ticket_details(t.ticket_id)}
