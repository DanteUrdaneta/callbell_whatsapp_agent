from fastapi import FastAPI, Request, status, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from core.callbell import send_callbell_message, escalate_to_success
from core.scheduler import start_scheduler
from dotenv import load_dotenv
from core.db import DB
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from modules.tools import history
from agents import agent
from groq import AsyncGroq
import traceback
import httpx
import os

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Error: SUPABASE_URL or SUPABASE_KEY not exist in .env")

groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY"))
app = FastAPI()
db = DB(url=SUPABASE_URL, key=SUPABASE_KEY)

# Cache para deduplicar mensajes procesados recientemente
processed_messages: set = set()
MAX_CACHE_SIZE = 200


class CallbellPayload(BaseModel):
    to_number: str = Field(..., alias="to")
    from_number: str = Field(..., alias="from")
    text: Optional[str] = None
    uuid: str
    status: str
    channel: str
    contact: Dict[str, Any]
    createdAt: str
    attachments: Optional[List[str]] = None


class CallbellWebhook(BaseModel):
    event: str
    payload: CallbellPayload


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    print(f"⚠️ 422 payload no reconocido: {body.decode()}")
    return JSONResponse(status_code=200, content={"status": "ignored"})


@app.on_event("startup")
async def startup_event():
    start_scheduler(db)


@app.get("/")
async def index():
    return "hello world"


@app.get("/webhook/callbell")
async def callbell_webhook_verify():
    return {"status": "ok"}


@app.post("/webhook/callbell", status_code=status.HTTP_200_OK)
async def callbell_webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=200, content={"status": "ignored"})

    event = body.get("event", "")
    payload = body.get("payload", {})

    print(f"📨 Evento recibido: event={event}")

    # ── Asesor asignado/desasignado manualmente en Callbell ──
    if event == "contact_updated":
        raw_phone = payload.get("phoneNumber", "")
        normalized = raw_phone.replace("+", "").replace(" ", "").replace("-", "")
        assigned_user = payload.get("assignedUser")
        if normalized:
            if assigned_user:
                db.update_status(phone_number=normalized, status="success")
                print(f"👤 Asesor asignado ({assigned_user}) — lead pasado a success: {normalized}")
            else:
                lead = db.get_lead(normalized)
                if lead and lead.get("status") == "success":
                    db.update_status(phone_number=normalized, status="onboarding")
                    print(f"👤 Asesor desasignado — lead vuelto a onboarding: {normalized}")
        return JSONResponse(status_code=200, content={"status": "ok"})

    # ── Reset a onboarding cuando se cierra la conversación ──
    if event == "conversation_closed":
        contact = payload.get("contact", {})
        raw_phone = contact.get("phoneNumber", "")
        normalized = raw_phone.replace("+", "").replace(" ", "").replace("-", "")
        if normalized:
            db.reset_lead(normalized)
            print(f"🔄 Conversación cerrada — lead reseteado a onboarding: {normalized}")
        return JSONResponse(status_code=200, content={"status": "ok"})

    # ── Mensajes normales ──
    try:
        webhook_data = CallbellWebhook(**body)
    except Exception as e:
        print(f"⚠️ Payload no reconocido: {e}")
        return JSONResponse(status_code=200, content={"status": "ignored"})

    msg_payload = webhook_data.payload
    lead_phone = msg_payload.from_number
    user_message = msg_payload.text
    lead_uuid = msg_payload.contact.get("uuid", msg_payload.uuid)
    message_uuid = msg_payload.uuid

    print(f"   status={msg_payload.status}, from={lead_phone}, text={user_message}")

    if msg_payload.status != "received":
        print(f"⚠️ Ignorando mensaje con status: {msg_payload.status}")
        return {"status": "ignored", "message": "Message was not received"}

    # ── Deduplicación de mensajes ──
    if message_uuid in processed_messages:
        print(f"⚠️ Mensaje duplicado ignorado: {message_uuid}")
        return {"status": "ignored", "message": "Duplicate message"}
    processed_messages.add(message_uuid)
    if len(processed_messages) > MAX_CACHE_SIZE:
        processed_messages.pop()

    # ── Verificar estado en Supabase ──
    lead = db.get_lead(lead_phone)
    if lead and lead.get("status") == "success":
        print(f"⚠️ Lead en status success, ignorando mensaje del bot")
        return {"status": "ignored", "message": "Lead already successful"}

    if msg_payload.attachments and len(msg_payload.attachments) > 0:
        file_url = msg_payload.attachments[0]
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(file_url)
            if response.status_code == 200:
                audio_bytes = response.content
                transcription = await groq_client.audio.transcriptions.create(
                    file=("audio.ogg", audio_bytes),
                    model="whisper-large-v3"
                )
                user_message = transcription.text
                print(f"🎙️ Transcripción: {user_message}")
        except Exception as audio_err:
            print(f"⚠️ Error procesando audio: {str(audio_err)}")
            traceback.print_exc()

    if not user_message:
        return {"status": "ignored", "message": "No text or audio to process"}

    try:
        db_history = db.get_chat_history(phone_number=lead_phone, limit=5)
        complete_user_message = f"{user_message}\n\n(uuid: {lead_uuid}, phone: {lead_phone})"
        ai_response = await agent.run(complete_user_message, message_history=history(db_history))

        try:
            db.update_history_message(
                phone_number=lead_phone,
                user_message=user_message,
                ai_message=ai_response.output
            )
        except ValueError:
            db.create_new_lead(lead_phone)
            db.update_history_message(
                phone_number=lead_phone,
                user_message=user_message,
                ai_message=ai_response.output
            )

        print(f"🤖 Respuesta del agente: {ai_response.output[:100]}...")
        await send_callbell_message(to_phone=lead_phone, text_content=ai_response.output)

        return {"status": "success", "message": "Event processed"}

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")
