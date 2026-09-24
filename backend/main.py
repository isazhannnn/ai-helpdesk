import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import APIConnectionError, APIStatusError, OpenAI
from pydantic import BaseModel, Field

from backend.database import (
    create_conversation,
    ensure_conversation,
    get_dashboard_stats,
    get_messages,
    initialize_database,
    save_message,
)

load_dotenv()
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
SYSTEM_PROMPT = """You are Helia, a helpful customer support assistant for a software product.
Be concise, warm, and practical. Reply in the user's language. Never invent account data,
payments, delivery status, or policies. If a human needs to investigate, say so clearly."""


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="Customer message")
    conversation_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str


def generate_ai_reply(history: list[dict[str, str]]) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="AI service is not configured. Add OPENAI_API_KEY to your .env file and restart the server.",
        )

    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            instructions=SYSTEM_PROMPT,
            input=history,
            store=False,
        )
        reply = response.output_text.strip()
        if not reply:
            raise HTTPException(status_code=502, detail="The AI service returned an empty response. Please try again.")
        return reply
    except HTTPException:
        raise
    except APIConnectionError as error:
        raise HTTPException(status_code=503, detail="Unable to reach the AI service. Please try again shortly.") from error
    except APIStatusError as error:
        raise HTTPException(status_code=502, detail="The AI service could not process this request. Please try again.") from error
    except Exception as error:
        raise HTTPException(status_code=500, detail="An unexpected error occurred while generating a reply.") from error


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="AI Helpdesk API", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/", include_in_schema=False)
def serve_frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/conversations", tags=["Conversations"])
def new_conversation() -> dict[str, str]:
    return {"conversation_id": create_conversation()}


@app.get("/api/dashboard", tags=["Dashboard"])
def dashboard() -> dict[str, int]:
    return get_dashboard_stats()


@app.post("/api/chat", response_model=ChatResponse, tags=["Chat"])
def chat(payload: ChatRequest) -> ChatResponse:
    conversation_id = ensure_conversation(payload.conversation_id)
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    save_message(conversation_id, "user", message)
    reply = generate_ai_reply(get_messages(conversation_id))
    save_message(conversation_id, "assistant", reply)
    return ChatResponse(conversation_id=conversation_id, reply=reply)
