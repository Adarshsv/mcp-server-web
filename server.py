from fastapi import FastAPI
from pydantic import BaseModel
import os

app = FastAPI()

class Query(BaseModel):
    query: str

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/run")
def run_mcp(q: Query):
    # 🔁 Replace this with your MCP logic
    response = {
        "input": q.query,
        "output": f"MCP processed: {q.query}"
    }
    return response
