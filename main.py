from contextlib import asynccontextmanager
from collections import deque
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
db = DB(url=SUPABASE_URL, key=SUPABASE_KEY)

# Cache FIFO para deduplicar mensajes procesados recientemente
MAX_CACHE_SIZE = 200
processed_messages: deque = deque(maxlen=MAX_CACHE_SIZE)


# ── Lifespan (reemplaza el deprecado @app.on_event) ──────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler(db)
    yield


app = FastAPI(lifespan=lifespan)


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

    # ── Deduplicación FIFO ──
    if message_uuid in processed_messages:
        print(f"⚠️ Mensaje duplicado ignorado: {message_uuid}")
        return {"status": "ignored", "message": "Duplicate message"}
    processed_messages.append(message_uuid)  # deque(maxlen=200) elimina el más antiguo automáticamente

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

        # Si el usuario pregunta por precios en pesos, inyectar la tasa de cambio automáticamente
        PESOS_KEYWORDS = ["pesos", "peso dominicano", "dop", "en pesos", "a pesos"]
        msg_lower = user_message.lower()
        tasa_info = ""
        if any(kw in msg_lower for kw in PESOS_KEYWORDS):
            try:
                from modules.tools import get_table
                config_data = get_table("CONFIG")
                tasa_info = f"\n\n[SISTEMA: Tasa de cambio actual de Airtable → {config_data}. Usa este valor para convertir, no inventes ninguno.]"
            except Exception as e:
                print(f"⚠️ No se pudo obtener CONFIG: {e}")

        complete_user_message = f"{user_message}\n\n(uuid: {lead_uuid}, phone: {lead_phone}){tasa_info}"
        ai_response = await agent.run(complete_user_message, message_history=history(db_history))

        try:
            db.update_history_message(
                phone_number=lead_phone,
                user_message=user_message,
                ai_message=ai_response.output,
            )
        except ValueError:
            db.create_new_lead(lead_phone)
            db.update_history_message(
                phone_number=lead_phone,
                user_message=user_message,
                ai_message=ai_response.output,
            )

        # Detectar despedida del usuario para evitar recordatorios innecesarios
        FAREWELL_KEYWORDS = [
            "gracias", "hasta luego", "hasta pronto", "adiós", "adios",
            "bye", "chao", "chau", "ok gracias", "muchas gracias",
            "ya entendí", "ya entendi", "perfecto gracias", "listo gracias",
            "eso era todo", "ya fue", "con eso es todo", "me retiro",
        ]
        user_msg_lower = user_message.lower().strip()
        es_despedida = any(kw in user_msg_lower for kw in FAREWELL_KEYWORDS)
        if es_despedida:
            db.set_inactive(phone_number=lead_phone)
            print(f"😴 Usuario se despidió — lead marcado como inactive: {lead_phone}")

        print(f"🤖 Respuesta del agente: {ai_response.output[:100]}...")

        # Limpiar markdown y LaTeX que el modelo pueda colar
        clean_response = ai_response.output
        import re
        clean_response = re.sub(r'\*+([^*]+)\*+', r'\1', clean_response)       # **texto** o *texto*
        clean_response = re.sub(r'_+([^_]+)_+', r'\1', clean_response)         # __texto__ o _texto_
        clean_response = re.sub(r'^#{1,6}\s+', '', clean_response, flags=re.MULTILINE)  # # headers
        clean_response = re.sub(r'\\\(.*?\\\)', lambda m: m.group(0)           # LaTeX inline \( \)
            .replace('\\(', '').replace('\\)', '')
            .replace('\\,', ' ').replace('\\text{', '').replace('}', '')
            .replace('\\times', 'x').replace('\\approx', '≈').strip(),
            clean_response)
        clean_response = re.sub(r'\\text\{([^}]+)\}', r'\1', clean_response)   # \text{...}
        clean_response = re.sub(r'\\times', 'x', clean_response)               # \times
        clean_response = re.sub(r'\\approx', '≈', clean_response)              # \approx
        clean_response = re.sub(r'\\,', ' ', clean_response)                   # \,
        clean_response = re.sub(r'\\\[.*?\\\]', lambda m: m.group(0)           # LaTeX block \[ \]
            .replace('\\[', '').replace('\\]', '')
            .replace('\\,', ' ').replace('\\text{', '').replace('}', '')
            .replace('\\times', 'x').replace('\\approx', '≈').strip(),
            clean_response, flags=re.DOTALL)

        await send_callbell_message(to_phone=lead_phone, text_content=clean_response)

        return {"status": "success", "message": "Event processed"}

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")
