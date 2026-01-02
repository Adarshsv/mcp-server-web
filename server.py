@app.post("/ticket/details")
async def ticket_details(req: TicketRequest):
    headers = zendesk_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        # -------- Main Ticket --------
        ticket_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}.json",
            headers=headers,
        )
        ticket_resp.raise_for_status()
        ticket = ticket_resp.json()["ticket"]

        # -------- Comments --------
        comments_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/tickets/{req.ticket_id}/comments.json",
            headers=headers,
        )
        comments = comments_resp.json().get("comments", [])
        summarized_comment = summarize_comments(comments)

        # -------- Related Tickets (LINKS ONLY) --------
        search_resp = await client.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json",
            headers=headers,
            params={"query": ticket["subject"]},
        )
        related_tickets = [
            {"id": t["id"], "subject": t["subject"],
             "url": f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{t['id']}"}
            for t in search_resp.json().get("results", [])
            if t.get("id") != ticket["id"]
        ][:3]

        # -------- Related Docs (LINKS ONLY) --------
        related_docs = []
        try:
            with DDGS() as ddgs:
                docs = ddgs.text(f"site:doc.castsoftware.com {ticket['subject']}", max_results=3)
                for d in docs:
                    related_docs.append({"title": d["title"], "url": d["href"]})
        except Exception:
            pass

        # -------- Suggested Resolution --------
        if related_tickets or related_docs:
            recommended_solution = (
                "Based on similar tickets and documentation, "
                "please follow known workarounds, apply recommended updates, "
                "or adjust configuration as per CAST guidelines."
            )
        else:
            recommended_solution = (
                "No direct reference found. Investigate ticket comments "
                "and CAST documentation for potential solution."
            )

        # -------- Summary --------
        summary = (
            f"Issue Summary:\n{ticket['subject']}\n\n"
            f"Observed Behavior:\n{summarized_comment}\n\n"
            f"Similar Issues:\n{len(related_tickets)} related tickets found.\n\n"
            f"Documentation References:\n{len(related_docs)} documents found.\n\n"
            f"Suggested Resolution:\n{recommended_solution}"
        )

        confidence = round(min(0.3 + len(related_tickets) * 0.2, 0.9), 2)

        return {
            "summary": summary,
            "confidence": confidence,
            "related_tickets": related_tickets,
            "related_docs": related_docs,
            "recommended_solution": recommended_solution
        }
