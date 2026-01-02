import os
import re
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict

# Optional OpenAI integration
USE_OPENAI = True  # Set True to use OpenAI for summaries
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if USE_OPENAI:
    import openai
    openai.api_key = OPENAI_API_KEY

app = FastAPI()

# ----------------- MODELS -----------------
class TicketRequest(BaseModel):
    ticket_id: int

class SearchRequest(BaseModel):
    query: str

# ----------------- HELPER FUNCTIONS -----------------
def clean_text(text: str) -> str:
    """Remove HTML, entities, and extra spaces."""
    text = re.sub(r"<.*?>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def summarize_semantic(comments: List[Dict]) -> str:
    """
    Summarize ticket/comments in 4-6 meaningful sentences.
    Uses OpenAI if enabled; else uses simple extraction.
    """
    if not comments:
        return "No ticket comments found."
    
    text = " ".join([c.get("plain_body") or c.get("body", "") for c in comments[:10]])
    text = clean_text(text)

    if USE_OPENAI:
        prompt = (
            "Summarize the following technical ticket discussion in 4-5 concise, human-readable sentences. "
            "Focus on the issue, cause, observed behavior, and suggested solution:\n\n" + text
        )
        try:
            response = openai.Completion.create(
                engine="text-davinci-003",
                prompt=prompt,
                temperature=0.5,
                max_tokens=250,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0
            )
            summary = response.choices[0].text.strip()
            return summary
        except Exception as e:
            print("OpenAI error:", e)
            # fallback to simple extraction
            text = " ".join(text.split(".")[:5])
            return text
    else:
        # fallback simple extraction (first 5 sentences containing keywords)
        sentences = re.split(r'(?<=[.!?]) +', text)
        keywords = ["error", "issue", "fail", "supported", "steps", "solution", "analysis"]
        important = [s for s in sentences if any(k.lower() in s.lower() for k in keywords)]
        if len(important) < 5:
            important = sentences[:5]
        return " ".join(important[:5])

def generate_summary(ticket_data: Dict) -> Dict:
    """
    Generate the concise summary response for a ticket.
    """
    comments = ticket_data.get("comments", [])
    
    summary_text = summarize_semantic(comments)
    
    # Related tickets and docs as links only
    related_tickets = [
        {"id": t.get("id"), "subject": t.get("subject"), "url": t.get("url")}
        for t in ticket_data.get("related_tickets", [])
    ]
    
    related_docs = [
        {"title": d.get("title"), "url": d.get("url")}
        for d in ticket_data.get("related_docs", [])
    ]
    
    # Suggested solution
    recommended_solution = ticket_data.get("recommended_solution") or \
        "Based on similar tickets and documentation, apply recommended updates, workarounds, or adjust configuration as per CAST guidelines."
    
    return {
        "summary": summary_text,
        "confidence": ticket_data.get("confidence", 0.5),
        "related_tickets": related_tickets,
        "related_docs": related_docs,
        "recommended_solution": recommended_solution
    }

# ----------------- ROUTES -----------------
@app.post("/ticket/details")
def ticket_details(request: TicketRequest):
    """
    Fetch ticket info and return concise semantic summary with links and solution.
    """
    ticket_data = fetch_ticket_data(request.ticket_id)
    return generate_summary(ticket_data)

@app.post("/search/all")
def search_all(request: SearchRequest):
    """
    Search tickets/docs by keyword and return concise summaries.
    """
    search_results = fetch_search_results(request.query)
    
    summarized_results = []
    for ticket in search_results.get("tickets", []):
        summarized_results.append(generate_summary(ticket))
    
    return {
        "query": request.query,
        "summary": f"Search Query: {request.query}. {len(search_results.get('tickets', []))} tickets found.",
        "results": summarized_results
    }

# ----------------- PLACEHOLDER FUNCTIONS -----------------
def fetch_ticket_data(ticket_id: int) -> Dict:
    """
    Replace with real ticket fetching logic.
    """
    return {
        "comments": [
            {"plain_body": "The VC++ project fails because paths could not be substituted. Install correct Visual Studio IDE."},
            {"plain_body": "Ensure IDE version matches VC++ project files to avoid registry errors."},
        ],
        "related_tickets": [
            {"id": 23930, "subject": "Is VC++ and VC supported by CAST?", "url": "https://castsoftware.zendesk.com/agent/tickets/23930"},
            {"id": 2208, "subject": "XXL table size info not visible on CAST result", "url": "https://castsoftware.zendesk.com/agent/tickets/2208"},
        ],
        "related_docs": [
            {"title": "CMS Snapshot Analyzer Fatal Error", "url": "https://doc.castsoftware.com/display/TKBQG/CMS+Snapshot+Analysis+-+Run+Analyzer+-+Fatal+Error+-+CPP+with+Core+CAST+AIP+-+Some+paths+could+not+be+substituted"}
        ],
        "recommended_solution": "Install correct IDE, match VC++ version, follow CAST troubleshooting guide.",
        "confidence": 0.9
    }

def fetch_search_results(query: str) -> Dict:
    """
    Replace with real search logic.
    """
    return {
        "tickets": [fetch_ticket_data(12345), fetch_ticket_data(12346)]
    }
