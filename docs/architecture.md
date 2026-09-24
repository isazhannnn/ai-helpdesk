# Architecture

```text
Browser dashboard → FastAPI application → SQLite
                         │
                         └────────────→ OpenAI Responses API
```

The frontend is served by FastAPI, so one process is enough for local development and Docker deployment. Each user message is stored before the model request. Recent conversation context is sent to the model, then its reply is stored after a successful response.

If `OPENAI_API_KEY` is unavailable, the chat route returns a structured `503` response rather than failing at startup. Health checks and the interface remain available during configuration.
