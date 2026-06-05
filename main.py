from contextlib import asynccontextmanager
from collections import deque
import datetime
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

# URL pública del servidor (Railway la expone como RAILWAY_PUBLIC_DOMAIN)
BASE_URL = os.environ.get("BASE_URL") or (
    f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}"
    if os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    else "http://localhost:8000"
)

groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY"))
db = DB(url=SUPABASE_URL, key=SUPABASE_KEY)

# Cache FIFO para deduplicar mensajes procesados recientemente
MAX_CACHE_SIZE = 200
processed_messages: deque = deque(maxlen=MAX_CACHE_SIZE)


# ── Lifespan (reemplaza el deprecado @app.on_event) ──────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cargar cotizaciones de Drive al iniciar
    import asyncio
    from modules.drive_reader import load_cotizaciones
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, load_cotizaciones)
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


@app.api_route("/pdf/{course_key:path}", methods=["GET", "HEAD"])
async def serve_pdf(request: Request, course_key: str):
    """Sirve el PDF de un curso directamente desde el cache en memoria."""
    import urllib.parse
    from fastapi.responses import Response
    from modules.drive_reader import _files_metadata, COURSE_FILE_KEYWORDS, _download_pdf_from_drive_by_id

    course_key = urllib.parse.unquote(course_key)

    file_keyword = COURSE_FILE_KEYWORDS.get(course_key, "").upper()
    matched_name = None
    matched_id = None
    for filename, file_id in _files_metadata.items():
        if file_keyword and file_keyword.upper() in filename.upper():
            matched_name = filename
            matched_id = file_id
            break

    if not matched_id:
        raise HTTPException(status_code=404, detail="Curso no encontrado")

    import re as _re
    clean = _re.sub(r'^\d+\s+', '', matched_name.strip())
    if not clean.lower().endswith(".pdf"):
        clean = f"{clean}.pdf"

    # HEAD request: solo confirmar que existe, sin descargar el PDF
    if request.method == "HEAD":
        return Response(
            content=b"",
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{clean}"'},
        )

    pdf_bytes = _download_pdf_from_drive_by_id(matched_id)
    if not pdf_bytes:
        raise HTTPException(status_code=500, detail="Error descargando PDF")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{clean}"'},
    )


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

    # ── Asesor asignado/desasignado en Callbell ──
    if event == "contact_updated":
        raw_phone = payload.get("phoneNumber") or ""
        if not raw_phone:
            return JSONResponse(status_code=200, content={"status": "ok"})
        normalized = raw_phone.replace("+", "").replace(" ", "").replace("-", "")
        assigned_user = payload.get("assignedUser")

        # Emails que Callbell asigna automáticamente (bot/sistema) — ignorar
        BOT_USERS = {"lrivascompres@gmail.com"}

        if normalized and assigned_user:
            # Si es un usuario del bot/sistema, ignorar
            if assigned_user in BOT_USERS:
                return JSONResponse(status_code=200, content={"status": "ok"})

            lead = db.get_lead(normalized)
            if lead:
                ultimo_mensaje = lead.get("ultimo_mensaje")
                ahora = datetime.datetime.now(datetime.timezone.utc)
                if ultimo_mensaje:
                    ultimo_dt = datetime.datetime.fromisoformat(ultimo_mensaje.replace("Z", "+00:00"))
                    segundos_diff = (ahora - ultimo_dt).total_seconds()
                    if segundos_diff < 5:
                        return JSONResponse(status_code=200, content={"status": "ok"})
                db.update_status(phone_number=normalized, status="success")
                print(f"👤 Asesor asignado manualmente ({assigned_user}) — lead pasado a success: {normalized}")
        elif normalized and not assigned_user:
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

        # Enviar PDF solo cuando el usuario pide explícitamente la cotización
        COTIZACION_KEYWORDS = [
            "cotizacion", "cotización", "pdf", "documento",
            "mándamelo", "mandamelo", "envíala", "enviala", "envíame", "enviame",
            "quiero la cotizacion", "dame la cotizacion", "me das la cotizacion",
            "me mandas la cotizacion", "me envias la cotizacion", "me enviás la cotizacion",
        ]
        pide_cotizacion = any(kw in user_message.lower() for kw in COTIZACION_KEYWORDS)

        pdf_enviado = False
        respuesta_pdf = None  # mensaje a enviar en lugar del agente cuando aplique

        if pide_cotizacion:
            from modules.drive_reader import detect_course_from_message, get_pdf_url_for_course
            from core.callbell import send_callbell_document

            # Buscar el curso en el mensaje actual
            course_key = detect_course_from_message(user_message.lower())
            if not course_key:
                # Buscar en los 3 mensajes previos del usuario
                db_history_check = db.get_chat_history(phone_number=lead_phone, limit=3)
                for msg in reversed(db_history_check or []):
                    user_msg = msg.get("user_message", "") or ""
                    course_key = detect_course_from_message(user_msg.lower())
                    if course_key:
                        break
                # Si aún no encontró, buscar en el último mensaje del bot (solo el más reciente)
                if not course_key and db_history_check:
                    last_ai = (db_history_check[-1].get("ai_message", "") or "")
                    course_key = detect_course_from_message(last_ai.lower())

            if not course_key:
                # No se detectó el curso — preguntarle al usuario
                respuesta_pdf = "¿De cuál curso quieres la cotización?"
            else:
                pdf_info = get_pdf_url_for_course(course_key)
                if not pdf_info:
                    # El curso existe pero no hay PDF disponible
                    respuesta_pdf = "Por el momento no tengo la cotización de ese curso disponible. Te recomiendo contactarnos al 829-535-1000 o info@enalas.com para más información."
                else:
                    # Verificar que no se haya enviado ya recientemente
                    db_history_check = db.get_chat_history(phone_number=lead_phone, limit=10)
                    ya_enviado = any(
                        "cotización" in (m.get("ai_message", "") or "").lower() and
                        "pdf" in (m.get("ai_message", "") or "").lower()
                        for m in (db_history_check or [])[-3:]
                    )
                    if not ya_enviado:
                        pdf_url, pdf_name, pdf_file_id = pdf_info
                        import re as _re
                        real_name = _re.sub(r'^\d+\s+', '', pdf_name.strip())
                        if not real_name.lower().endswith(".pdf"):
                            real_name = f"{real_name}.pdf"
                        import urllib.parse as _up
                        self_url = f"{BASE_URL}/pdf/{_up.quote(course_key)}"
                        await send_callbell_document(
                            to_phone=lead_phone,
                            file_url=self_url,
                            filename=real_name,
                        )
                        print(f"📎 PDF enviado: {real_name} via {self_url}")
                        pdf_enviado = True
                        respuesta_pdf = "¡Aquí tienes la cotización! Si tienes alguna pregunta, con gusto te ayudo."
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

        # Si el bloque de PDF manejó la respuesta, enviarla y salir
        if respuesta_pdf is not None:
            await send_callbell_message(to_phone=lead_phone, text_content=respuesta_pdf)
            return {"status": "success", "message": "Event processed"}

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
        clean_response = re.sub(r'^\s*-\s+', '• ', clean_response, flags=re.MULTILINE)  # - item → • item

        await send_callbell_message(to_phone=lead_phone, text_content=clean_response)

        return {"status": "success", "message": "Event processed"}

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")
