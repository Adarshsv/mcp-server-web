@app.get("/env")
def env_check():
    return {
        "ZENDESK_EMAIL": os.getenv("ZENDESK_EMAIL"),
        "ZENDESK_API_TOKEN": "*****" if os.getenv("ZENDESK_API_TOKEN") else None,
        "ZENDESK_SUBDOMAIN": os.getenv("ZENDESK_SUBDOMAIN"),
        "OPENAI_API_KEY": "*****" if os.getenv("OPENAI_API_KEY") else None,
    }