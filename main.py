import re
import asyncio
import datetime
import traceback
import urllib.parse
from contextlib import asynccontextmanager
from collections import deque

import httpx
from fastapi import FastAPI, Request, status, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from groq import AsyncGroq

from core.callbell import send_callbell_message, send_callbell_document, escalate_to_success
from core.scheduler import start_scheduler
from core.db import DB
from modules.tools import history
from agents import agent, db  # FIX: reutilizar la instancia de db de agents.py en lugar de crear una nueva

import os
import random
load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Error: SUPABASE_URL or SUPABASE_KEY not exist in .env")

BASE_URL = os.environ.get("BASE_URL") or (
    f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}"
    if os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    else "http://localhost:8000"
)

groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY"))

MAX_CACHE_SIZE = 200
processed_messages: deque = deque(maxlen=MAX_CACHE_SIZE)

ASESOR_KEYWORDS = [
    "asesor", "agente", "humano", "persona real", "hablar con alguien",
    "llamar", "llamame", "llámame", "quiero hablar", "me pueden llamar",
    "pueden contactarme", "contactarme con", "quiero contactar",
    "me contactas", "contactas con", "me pones", "ponme con",
    "quiero inscribirme", "me quiero inscribir", "quiero inscribir",
    "como me inscribo", "cómo me inscribo", "quiero matricularme",
    "quiero empezar el curso", "quiero pagar", "quiero comenzar",
    "quiero empezar", "como empiezo", "cómo empiezo",
]


def quiere_asesor(texto: str) -> bool:
    t = texto.lower()
    return any(kw in t for kw in ASESOR_KEYWORDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from modules.drive_reader import load_cotizaciones
    loop = asyncio.get_running_loop()
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
    from modules.drive_reader import get_pdf_url_for_course, _download_pdf_from_drive_by_id

    course_key = urllib.parse.unquote(course_key)
    pdf_info = get_pdf_url_for_course(course_key)
    if not pdf_info:
        raise HTTPException(status_code=404, detail="Curso no encontrado")

    _, matched_name, matched_id = pdf_info

    clean = re.sub(r'^\d+\s+', '', matched_name.strip())
    if not clean.lower().endswith(".pdf"):
        clean = f"{clean}.pdf"

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

    if event == "contact_updated":
        raw_phone = payload.get("phoneNumber") or ""
        if not raw_phone:
            return JSONResponse(status_code=200, content={"status": "ok"})
        normalized = raw_phone.replace("+", "").replace(" ", "").replace("-", "")
        assigned_user = payload.get("assignedUser")

        BOT_USERS = {"lrivascompres@gmail.com"}

        if normalized and assigned_user:
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
                ultimo_mensaje = lead.get("ultimo_mensaje")
                ahora = datetime.datetime.now(datetime.timezone.utc)
                if ultimo_mensaje:
                    ultimo_dt = datetime.datetime.fromisoformat(ultimo_mensaje.replace("Z", "+00:00"))
                    segundos_diff = (ahora - ultimo_dt).total_seconds()
                    if segundos_diff > 30:
                        db.update_status(phone_number=normalized, status="onboarding")
                        print(f"👤 Asesor desasignado — lead vuelto a onboarding: {normalized}")
                    else:
                        print(f"⏱️ Ignorando revert a onboarding — escalado reciente ({segundos_diff:.1f}s)")
                else:
                    db.update_status(phone_number=normalized, status="onboarding")
                    print(f"👤 Asesor desasignado — lead vuelto a onboarding: {normalized}")
        return JSONResponse(status_code=200, content={"status": "ok"})

    if event == "conversation_closed":
        contact = payload.get("contact", {})
        raw_phone = contact.get("phoneNumber", "")
        normalized = raw_phone.replace("+", "").replace(" ", "").replace("-", "")
        if normalized:
            db.reset_lead(normalized)
            print(f"🔄 Conversación cerrada — lead reseteado a onboarding: {normalized}")
        return JSONResponse(status_code=200, content={"status": "ok"})

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

    if message_uuid in processed_messages:
        print(f"⚠️ Mensaje duplicado ignorado: {message_uuid}")
        return {"status": "ignored", "message": "Duplicate message"}
    processed_messages.append(message_uuid)

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
        lead = db.get_lead(lead_phone)
        lead_status = lead.get("status", "onboarding") if lead else "onboarding"

        # ── FLUJO DE ESCALADO A ASESOR (hardcodeado, sin depender del modelo) ──

        # PASO 2: Usuario confirmando número
        if lead_status == "esperando_confirmacion_asesor":
            msg_lower = user_message.lower().strip()
            confirma = any(kw in msg_lower for kw in ["si", "sí", "yes", "correcto", "exacto", "confirmo", "ok", "claro"])
            if confirma:
                numero_guardado = ""
                for msg in reversed(db_history or []):
                    ai_msg = msg.get("ai_message", "") or ""
                    if "confirmas" in ai_msg.lower() or "¿es" in ai_msg.lower():
                        ai_msg_clean = re.sub(r'(\d)[\s\-](\d)', r'\1\2', ai_msg)
                        numeros = re.findall(r'\d{7,15}', ai_msg_clean)
                        if numeros:
                            numero_guardado = numeros[0]
                            break

                nombre_guardado = ""
                for msg in reversed(db_history or []):
                    ai_msg = msg.get("ai_message", "") or ""
                    if "nombre" in ai_msg.lower() and "número" in ai_msg.lower():
                        idx = (db_history or []).index(msg)
                        for siguiente in (db_history or [])[idx:]:
                            user_msg = siguiente.get("user_message", "") or ""
                            if user_msg and not user_msg.startswith("["):
                                nombre_candidato = re.sub(r'[\d\-\+\s,\.]+', ' ', user_msg).strip()
                                palabras = nombre_candidato.split()
                                if palabras:
                                    nombre_guardado = palabras[0].capitalize()
                                break
                        if nombre_guardado:
                            break

                respuesta = (
                    f"Perfecto, estoy conectándote con un asesor ahora mismo. Tendrás contacto en breve. Gracias, {nombre_guardado}."
                    if nombre_guardado
                    else "Perfecto, estoy conectándote con un asesor ahora mismo. Tendrás contacto en breve."
                )
                db.update_status(phone_number=lead_phone, status="success")
                await escalate_to_success(lead_uuid)
                db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=respuesta)
                await send_callbell_message(to_phone=lead_phone, text_content=respuesta)
                print(f"✅ Lead escalado a asesor: {lead_phone}")
                return {"status": "success", "message": "Event processed"}
            else:
                db.update_status(phone_number=lead_phone, status="onboarding")

        # PASO 1b: Usuario ya en espera de datos, buscar número
        if lead_status == "esperando_datos_asesor":
            msg_clean = re.sub(r'(\d)[\s\-](\d)', r'\1\2', user_message)
            numeros = re.findall(r'\d{7,15}', msg_clean)
            if numeros:
                numero = numeros[0]
                respuesta = f"Quiero asegurarme de tener bien tu número, ¿me lo confirmas? ¿Es {numero}?"
                db.update_status(phone_number=lead_phone, status="esperando_confirmacion_asesor")
                db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=respuesta)
                await send_callbell_message(to_phone=lead_phone, text_content=respuesta)
                return {"status": "success", "message": "Event processed"}
            else:
                respuesta = "Para conectarte con un asesor necesito tu nombre y número de teléfono. ¿Me los puedes dar?"
                db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=respuesta)
                await send_callbell_message(to_phone=lead_phone, text_content=respuesta)
                return {"status": "success", "message": "Event processed"}

        # PASO 1: Usuario pide asesor por primera vez
        if quiere_asesor(user_message):
            respuesta = "Con gusto, puedo conectarte con un asesor. Primero, ¿me puedes dar tu nombre y un número de contacto para que puedan comunicarse contigo?"
            db.update_status(phone_number=lead_phone, status="esperando_datos_asesor")
            try:
                db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=respuesta)
            except ValueError:
                db.create_new_lead(lead_phone)
                db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=respuesta)
            await send_callbell_message(to_phone=lead_phone, text_content=respuesta)
            return {"status": "success", "message": "Event processed"}

        # ── Fin flujo asesor ──────────────────────────────────────────────────

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

        from modules.drive_reader import detect_course_from_message, get_pdf_url_for_course, get_multi_sede_courses, _normalize, _course_file_map, get_course_display_name

        COTIZACION_KEYWORDS = [
            "cotizacion", "cotización", "pdf", "documento",
            "mándamelo", "mandamelo", "envíala", "enviala", "envíame", "enviame",
            "quiero la cotizacion", "dame la cotizacion", "me das la cotizacion",
            "me mandas la cotizacion", "me envias la cotizacion", "me enviás la cotizacion",
            "cuanto cuesta", "cuánto cuesta", "precio", "precios",
            "info del curso", "información del curso", "detalles del curso",
            "mandame info", "mándame info", "quiero info", "dame info",
            "me mandas info", "me envias info",
        ]
        pide_cotizacion = any(kw in user_message.lower() for kw in COTIZACION_KEYWORDS)

        MULTI_SEDE_COURSES = get_multi_sede_courses()

        # ── FLUJO DE SEDE (stateful, igual que el flujo de asesor) ──────────────
        # Si el lead está esperando elegir sede, resolver eso antes que nada
        if lead_status and lead_status.startswith("esperando_sede:"):
            pending_course = lead_status.replace("esperando_sede:", "").strip()
            msg_norm = _normalize(user_message)

            sede_elegida = None
            if "isabela" in msg_norm or "santo domingo" in msg_norm:
                sede_elegida = "santo domingo"
            elif "punta cana" in msg_norm:
                sede_elegida = "punta cana"

            if sede_elegida:
                full_key = f"{pending_course} {sede_elegida}"
                db.update_status(phone_number=lead_phone, status="onboarding")
                if full_key in _course_file_map and pide_cotizacion:
                    # Enviar PDF de la sede elegida
                    pdf_info = get_pdf_url_for_course(full_key)
                    if pdf_info:
                        pdf_url, pdf_name, pdf_file_id = pdf_info
                        real_name = re.sub(r'^\d+\s+', '', pdf_name.strip())
                        if not real_name.lower().endswith(".pdf"):
                            real_name = f"{real_name}.pdf"
                        self_url = f"{BASE_URL}/pdf/{urllib.parse.quote(full_key)}"
                        await send_callbell_document(to_phone=lead_phone, file_url=self_url, filename=real_name)
                        print(f"📎 PDF enviado tras elegir sede: {real_name}")
                        respuesta_sede = random.choice([
                            "Ya te envié la cotización.",
                            "Listo, ahí la tienes. Revísala con calma y me comentas cualquier duda.",
                            "Acabo de enviarte la cotización. Estoy pendiente por si tienes preguntas.",
                        ])
                        try:
                            db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=respuesta_sede)
                        except ValueError:
                            db.create_new_lead(lead_phone)
                            db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=respuesta_sede)
                        await send_callbell_message(to_phone=lead_phone, text_content=respuesta_sede)
                        return {"status": "success", "message": "Event processed"}
                # Si no pide cotización, dejar que el agente responda con la sede inyectada
                user_message = f"{user_message} [sede elegida: {sede_elegida}]"
            else:
                # No entendió la sede, volver a preguntar
                sedes = sorted([k.replace(pending_course, "").strip().title()
                               for k in _course_file_map.keys()
                               if k.startswith(pending_course + " ")])
                sedes_str = " o ".join(sedes)
                repregunta = f"Disculpa, no entendí bien. ¿El curso lo tomarías en {sedes_str}?"
                try:
                    db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=repregunta)
                except ValueError:
                    db.create_new_lead(lead_phone)
                    db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=repregunta)
                await send_callbell_message(to_phone=lead_phone, text_content=repregunta)
                return {"status": "success", "message": "Event processed"}
        # ── Fin flujo sede ──────────────────────────────────────────────────────

        detected_course = detect_course_from_message(user_message.lower())

        # Si se detecta un curso multi-sede y el usuario no indicó la sede, preguntar y bloquear
        if detected_course and detected_course in MULTI_SEDE_COURSES:
            msg_norm = _normalize(user_message)
            sede_en_mensaje = None
            if "isabela" in msg_norm or "santo domingo" in msg_norm:
                sede_en_mensaje = "santo domingo"
            elif "punta cana" in msg_norm:
                sede_en_mensaje = "punta cana"

            if not sede_en_mensaje:
                sedes = sorted([k.replace(detected_course, "").strip().title()
                               for k in _course_file_map.keys()
                               if k.startswith(detected_course + " ")])
                sedes_str = " o ".join(sedes)
                pregunta_sede = f"El curso de {get_course_display_name(detected_course)} lo ofrecemos en dos sedes: {sedes_str}. ¿En cuál te interesa?"
                db.update_status(phone_number=lead_phone, status=f"esperando_sede:{detected_course}")
                try:
                    db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=pregunta_sede)
                except ValueError:
                    db.create_new_lead(lead_phone)
                    db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=pregunta_sede)
                await send_callbell_message(to_phone=lead_phone, text_content=pregunta_sede)
                return {"status": "success", "message": "Event processed"}
            else:
                # Sede ya mencionada en el mensaje, inyectar en el mensaje del agente
                full_key = f"{detected_course} {sede_en_mensaje}"
                if full_key in _course_file_map and pide_cotizacion:
                    user_message = f"{user_message} {full_key}"

        # (bloque de sede legacy eliminado — reemplazado por flujo stateful esperando_sede:)

        pdf_enviado = False

        PDF_RESPONSES = [
            "Ya te envié la cotización.",
            "Te acabo de compartir el documento con toda la información.",
            "Listo, ahí la tienes. Revísala con calma y me comentas cualquier duda.",
            "Perfecto, ya te mandé la cotización completa.",
            "Te la envié hace un momento. Si quieres te explico cualquier parte.",
            "Acabo de enviarte la cotización. Estoy pendiente por si tienes preguntas.",
            "Ya la tienes en el chat.",
        ]

        # ✅ FIX: empieza en None — solo se asigna si realmente se procesa una cotización.
        # Antes era random.choice(PDF_RESPONSES) desde el inicio, lo que causaba que el bot
        # enviara mensajes como "Ya la tienes en el chat 😊" aunque no hubiera enviado ningún PDF.
        respuesta_pdf = None

        if pide_cotizacion:
            course_key = detect_course_from_message(user_message.lower())
            if not course_key:
                for msg in reversed(db_history or []):
                    user_msg = msg.get("user_message", "") or ""
                    course_key = detect_course_from_message(user_msg.lower())
                    if course_key:
                        break
                if not course_key and db_history:
                    last_ai = (db_history[-1].get("ai_message", "") or "")
                    course_key = detect_course_from_message(last_ai.lower())

            # Si el curso es multi-sede, intentar resolver la sede desde el historial
            if course_key in MULTI_SEDE_COURSES:
                sede_resuelta = None
                for msg in reversed(db_history or []):
                    for field in ("user_message", "ai_message"):
                        text = _normalize(msg.get(field, "") or "")
                        if "santo domingo" in text or "isabela" in text:
                            sede_resuelta = "santo domingo"
                            break
                        elif "punta cana" in text:
                            sede_resuelta = "punta cana"
                            break
                    if sede_resuelta:
                        break

                if sede_resuelta:
                    course_key = f"{course_key} {sede_resuelta}"
                else:
                    # No hay sede en el historial, preguntar
                    sedes = sorted([k.replace(course_key, "").strip().title()
                                   for k in _course_file_map.keys()
                                   if k.startswith(course_key + " ")])
                    sedes_str = " o ".join(sedes)
                    pregunta = f"Para enviarte la cotización correcta, ¿el curso lo tomarías en {sedes_str}?"
                    db.update_status(phone_number=lead_phone, status=f"esperando_sede:{course_key}")
                    try:
                        db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=pregunta)
                    except ValueError:
                        db.create_new_lead(lead_phone)
                        db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=pregunta)
                    await send_callbell_message(to_phone=lead_phone, text_content=pregunta)
                    return {"status": "success", "message": "Event processed"}

            if course_key:
                pdf_info = get_pdf_url_for_course(course_key)
                if not pdf_info:
                    respuesta_pdf = "Por el momento no tengo la cotización de ese curso disponible. Contáctanos al 829-535-1000 o info@enalas.com."
                else:
                    pdf_url, pdf_name, pdf_file_id = pdf_info
                    real_name = re.sub(r'^\d+\s+', '', pdf_name.strip())
                    if not real_name.lower().endswith(".pdf"):
                        real_name = f"{real_name}.pdf"
                    self_url = f"{BASE_URL}/pdf/{urllib.parse.quote(course_key)}"
                    await send_callbell_document(
                        to_phone=lead_phone,
                        file_url=self_url,
                        filename=real_name,
                    )
                    print(f"📎 PDF enviado: {real_name}")
                    pdf_enviado = True
                    respuesta_pdf = random.choice(PDF_RESPONSES)

        if respuesta_pdf is not None:
            try:
                db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=respuesta_pdf)
            except ValueError:
                db.create_new_lead(lead_phone)
                db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=respuesta_pdf)
            await send_callbell_message(to_phone=lead_phone, text_content=respuesta_pdf)
            return {"status": "success", "message": "Event processed"}

        # FIX: uuid y phone NO se inyectan en el mensaje visible al modelo
        complete_user_message = f"{user_message}{tasa_info}"
        ai_response = await agent.run(complete_user_message, message_history=history(db_history))

        try:
            usage = ai_response.usage
            tokens_this_call = usage.total_tokens or 0
            db.add_tokens(phone_number=lead_phone, tokens=tokens_this_call)
            print(f"🔢 Tokens usados: {tokens_this_call}")
        except Exception as e:
            print(f"⚠️ Error registrando tokens: {e}")

        try:
            db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=ai_response.output)
        except ValueError:
            db.create_new_lead(lead_phone)
            db.update_history_message(phone_number=lead_phone, user_message=user_message, ai_message=ai_response.output)

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

        try:
            from modules.dashboard import update_dashboard
            update_dashboard(db)
        except Exception as e:
            print(f"⚠️ Error en dashboard: {e}")

        # Limpiar markdown de la respuesta antes de enviar por WhatsApp
        clean_response = ai_response.output
        clean_response = re.sub(r'\*+([^*]+)\*+', r'\1', clean_response)
        clean_response = re.sub(r'_+([^_]+)_+', r'\1', clean_response)
        clean_response = re.sub(r'^#{1,6}\s+', '', clean_response, flags=re.MULTILINE)
        clean_response = re.sub(
            r'\\\(.*?\\\)',
            lambda m: m.group(0)
                .replace('\\(', '').replace('\\)', '')
                .replace('\\,', ' ').replace('\\text{', '').replace('}', '')
                .replace('\\times', 'x').replace('\\approx', '≈').strip(),
            clean_response
        )
        clean_response = re.sub(r'\\text\{([^}]+)\}', r'\1', clean_response)
        clean_response = re.sub(r'\\times', 'x', clean_response)
        clean_response = re.sub(r'\\approx', '≈', clean_response)
        clean_response = re.sub(r'\\,', ' ', clean_response)
        clean_response = re.sub(
            r'\\\[.*?\\\]',
            lambda m: m.group(0)
                .replace('\\[', '').replace('\\]', '')
                .replace('\\,', ' ').replace('\\text{', '').replace('}', '')
                .replace('\\times', 'x').replace('\\approx', '≈').strip(),
            clean_response,
            flags=re.DOTALL
        )
        clean_response = re.sub(r'^\s*-\s+', '• ', clean_response, flags=re.MULTILINE)

        await send_callbell_message(to_phone=lead_phone, text_content=clean_response)

        return {"status": "success", "message": "Event processed"}

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")
