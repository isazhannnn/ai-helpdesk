import os
import re
from time import perf_counter
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response as FastAPIResponse
from fastapi.staticfiles import StaticFiles
from openai import APIConnectionError, APIStatusError, OpenAI
from pydantic import BaseModel, Field

from backend.database import (
    create_conversation,
    create_session,
    create_user,
    authenticate_user,
    delete_session,
    ensure_conversation,
    get_dashboard_stats,
    get_analytics,
    get_messages,
    initialize_database,
    get_user_by_session,
    list_conversations,
    latest_route,
    record_ai_response,
    record_route,
    save_message,
    set_response_feedback,
)
from backend.scenario_router import route_conversation, selected_scenario

load_dotenv()
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
SYSTEM_PROMPT = """You are Helia, the voice AI agent for AI Helpdesk insurance contact center.

PRODUCT KNOWLEDGE
This is a voice-router simulation. Its core job is to use an LLM to select the right one of 40 insurance
scenarios from dialogue context, including Russian/Kazakh code-switching and topic changes. The selected scenario,
alternatives, rationale and timing are shown to a supervisor in Conversation trace.

If the customer asks "what are you?", "what product is this?", "what do you represent?", or equivalent,
introduce yourself as AI Helpdesk's virtual assistant and explain that you help route and resolve insurance requests.

Be concise, warm, and practical. Match the user's language: Russian-only requests get Russian replies, Kazakh-only requests get Kazakh replies, and Russian/Kazakh mixed requests get naturally mixed replies. Never determine reply language solely from the last word or sentence. Never invent policy data, payment results, claim status,
or actions that were not actually completed."""


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="Customer message")
    conversation_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    assistant_message_id: int
    route: dict[str, object]


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class FeedbackRequest(BaseModel):
    helpful: bool


def generate_ai_reply(history: list[dict[str, str]], user: dict[str, str], scenario: dict[str, object]) -> str:
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
            instructions=f"{SYSTEM_PROMPT}\n\nSelected scenario: {scenario['scenario_id']} — {scenario['name']}.\n"
                         f"Purpose: {scenario['description']}\nRequired slots: {scenario['slots']['required']}\n"
                         f"Scenario actions: {scenario['actions']}\nUse this scenario's opening response as guidance: {scenario['responses']}\n"
                         f"The signed-in customer is {user['name']} ({user['email']}). If they ask who they are, identify them using these account details only.\n"
                         f"Reply language: {scenario['reply_language']}. Use Russian only for ru, Kazakh only for kk, and natural Russian/Kazakh code-switching for mixed.\n"
                         "If the customer asks what you can do, introduce yourself and briefly list the insurance requests you can help route: buying or renewing a policy, price calculation, payment, claims, policy details and contacting an operator. Do not ask 'When is convenient?' for this question.",
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


async def transcribe_voice(audio: UploadFile) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Voice transcription is not configured. Add OPENAI_API_KEY to .env.")
    content = await audio.read()
    if not content:
        raise HTTPException(status_code=422, detail="The recording is empty. Please try again.")
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The recording is too large. Keep it under 25 MB.")
    try:
        transcript = OpenAI(api_key=api_key).audio.transcriptions.create(
            model="gpt-transcribe",
            file=(audio.filename or "recording.webm", content, audio.content_type or "audio/webm"),
            prompt="""Insurance contact center. The speaker may naturally mix Kazakh and Russian.
Preserve Kazakh spelling in Cyrillic exactly; do not transliterate it as Russian. Common Kazakh words include:
қалай, сәлем, рақмет, өтінемін, сақтандыру, төлем, полис, көлік, бүгін, кеше, маған, керек, болады.
Keep Russian words in Russian and preserve code-switching.""",
            extra_body={"languages": ["kk", "ru"], "keywords": ["қалай", "сәлем", "сақтандыру", "полис", "төлем", "көлік"]},
        )
        if not transcript.text.strip():
            raise HTTPException(status_code=422, detail="Speech could not be recognized. Please speak closer to the microphone.")
        text = transcript.text.strip()
        corrections = {"кавай": "қалай", "калей": "қалай", "салем": "сәлем", "рахмет": "рақмет", "сактандыру": "сақтандыру"}
        for incorrect, correct in corrections.items():
            text = re.sub(rf"\b{incorrect}\b", correct, text, flags=re.IGNORECASE)
        return text
    except HTTPException:
        raise
    except APIConnectionError as error:
        raise HTTPException(status_code=503, detail="Unable to reach the voice service. Please try again shortly.") from error
    except APIStatusError as error:
        raise HTTPException(status_code=502, detail="The voice service could not process this recording. Please try again.") from error


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


def current_user(session_token: str | None = Cookie(default=None)) -> dict[str, str]:
    user = get_user_by_session(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return user


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie("session_token", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)


@app.post("/api/auth/register", tags=["Authentication"])
def register(payload: RegisterRequest, response: Response) -> dict[str, str]:
    try:
        user = create_user(payload.name.strip(), payload.email, payload.password)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    set_session_cookie(response, create_session(user["id"]))
    return user


@app.post("/api/auth/login", tags=["Authentication"])
def login(payload: LoginRequest, response: Response) -> dict[str, str]:
    user = authenticate_user(payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    set_session_cookie(response, create_session(user["id"]))
    return user


@app.post("/api/auth/logout", tags=["Authentication"])
def logout(response: Response, session_token: str | None = Cookie(default=None)) -> dict[str, bool]:
    delete_session(session_token)
    response.delete_cookie("session_token")
    return {"ok": True}


@app.get("/api/me", tags=["Authentication"])
def me(user: dict[str, str] = Depends(current_user)) -> dict[str, str]:
    return user


@app.post("/api/conversations", tags=["Conversations"])
def new_conversation(user: dict[str, str] = Depends(current_user)) -> dict[str, str]:
    return {"conversation_id": create_conversation(user["id"])}


@app.get("/api/conversations", tags=["Conversations"])
def conversations(user: dict[str, str] = Depends(current_user)) -> list[dict[str, str]]:
    return list_conversations(user["id"])


@app.get("/api/conversations/{conversation_id}/messages", tags=["Conversations"])
def conversation_messages(conversation_id: str, user: dict[str, str] = Depends(current_user)) -> list[dict[str, object]]:
    try:
        ensure_conversation(conversation_id, user["id"])
    except PermissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return get_messages(conversation_id, limit=100)


@app.get("/api/conversations/{conversation_id}/trace", tags=["Conversations"])
def conversation_trace(conversation_id: str, user: dict[str, str] = Depends(current_user)) -> dict[str, object]:
    try:
        ensure_conversation(conversation_id, user["id"])
    except PermissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    history = get_messages(conversation_id, limit=100)
    route = latest_route(conversation_id, user["id"])
    latest_question = next((item["content"] for item in reversed(history) if item["role"] == "user"), "No customer message yet.")
    return {
        "conversation_id": conversation_id,
        "status": "completed" if any(item["role"] == "assistant" for item in history) else "waiting",
        "question": latest_question,
        "message_count": len(history),
        "route": route,
        "steps": [
            {"title": "Identity verified", "detail": f"Signed in as {user['name']}; this conversation belongs to this account.", "kind": "security"},
            {"title": "Conversation context loaded", "detail": f"{len(history)} saved message(s) were available as private context.", "kind": "context"},
            {"title": "LLM scenario routing", "detail": f"{route['scenario_id']} selected in {route['routing_latency_ms']} ms: {route['reason']}" if route else "No completed routing decision yet.", "kind": "ai"},
            {"title": "Response recorded", "detail": "Messages are saved in the local SQLite conversation history.", "kind": "storage"},
        ],
        "explanation": "The assistant receives only the current user's profile and this conversation's messages. API keys, passwords, and other users' conversations never enter the route.",
    }


@app.get("/api/dashboard", tags=["Dashboard"])
def dashboard(user: dict[str, str] = Depends(current_user)) -> dict[str, int | float]:
    return get_dashboard_stats(user["id"])


@app.get("/api/analytics", tags=["Analytics"])
def analytics(user: dict[str, str] = Depends(current_user)) -> dict[str, object]:
    return get_analytics(user["id"])


@app.post("/api/chat", response_model=ChatResponse, tags=["Chat"])
def chat(payload: ChatRequest, user: dict[str, str] = Depends(current_user)) -> ChatResponse:
    try:
        conversation_id = ensure_conversation(payload.conversation_id, user["id"])
    except PermissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    save_message(conversation_id, "user", message)
    started_at = perf_counter()
    history = [{"role": item["role"], "content": item["content"]} for item in get_messages(conversation_id)]
    decision, routing_latency_ms = route_conversation(history)
    scenario = dict(selected_scenario(str(decision["scenario_id"])))
    reply_language = str(decision["reply_language"])
    scenario["reply_language"] = reply_language
    record_route(conversation_id, user["id"], decision, routing_latency_ms)
    reply = generate_ai_reply(history, user, scenario)
    latency_ms = round((perf_counter() - started_at) * 1000)
    assistant_message_id = save_message(conversation_id, "assistant", reply)
    record_ai_response(assistant_message_id, conversation_id, user["id"], latency_ms)
    route = {"scenario_id": decision["scenario_id"], "scenario_name": scenario["name"], "confidence": decision["confidence"],
             "reason": decision["reason"], "alternatives": decision["alternative_ids"], "routing_latency_ms": routing_latency_ms,
             "language": decision["language"], "reply_language": decision["reply_language"], "topic_switched": decision["topic_switched"]}
    return ChatResponse(conversation_id=conversation_id, reply=reply, assistant_message_id=assistant_message_id, route=route)


@app.post("/api/responses/{message_id}/feedback", tags=["Analytics"])
def feedback(message_id: int, payload: FeedbackRequest, user: dict[str, str] = Depends(current_user)) -> dict[str, bool]:
    if not set_response_feedback(message_id, user["id"], payload.helpful):
        raise HTTPException(status_code=404, detail="Response not found.")
    return {"ok": True}


@app.post("/api/transcribe", tags=["Voice"])
async def transcribe(audio: UploadFile = File(...), _: dict[str, str] = Depends(current_user)) -> dict[str, str]:
    return {"text": await transcribe_voice(audio)}


@app.post("/api/speech", tags=["Voice"])
def speech(payload: SpeechRequest, _: dict[str, str] = Depends(current_user)) -> FastAPIResponse:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="AI voice is not configured. Add OPENAI_API_KEY to .env.")
    try:
        audio = OpenAI(api_key=api_key).audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="coral",
            input=payload.text,
            instructions="Speak naturally, clearly, warmly, and at a moderate pace. Preserve Kazakh and Russian words accurately.",
            response_format="mp3",
        )
        return FastAPIResponse(content=audio.read(), media_type="audio/mpeg")
    except APIConnectionError as error:
        raise HTTPException(status_code=503, detail="Unable to reach the AI voice service. Please try again shortly.") from error
    except APIStatusError as error:
        raise HTTPException(status_code=502, detail="The AI voice service could not generate audio. Please try again.") from error
