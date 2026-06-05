import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from core.db import DB
from core.callbell import send_callbell_message
from modules.drive_reader import load_cotizaciones, REFRESH_HOURS

# Minutos de inactividad antes de enviar el primer recordatorio
MINUTOS_INACTIVIDAD = 15


def start_scheduler(db: DB):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        verificar_recordatorios,
        "interval",
        minutes=1,
        args=[db],
        id="recordatorios",
        misfire_grace_time=30,
    )
    scheduler.add_job(
        _refresh_cotizaciones,
        "interval",
        hours=REFRESH_HOURS,
        id="refresh_cotizaciones",
        misfire_grace_time=60,
    )
    scheduler.start()
    print("⏰ Scheduler de recordatorios iniciado")
    return scheduler


async def _refresh_cotizaciones():
    """Refresca los PDFs de Drive en segundo plano."""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, load_cotizaciones)
    print("🔄 Cotizaciones de Drive refrescadas")


async def verificar_recordatorios(db: DB):
    # Import dentro de la función para evitar imports circulares al iniciar el módulo
    from agents import agent
    from modules.tools import history as build_history

    print("🔍 Verificando leads para recordatorio...")
    hace_n_min = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=MINUTOS_INACTIVIDAD)
    ).isoformat()

    try:
        leads = db.get_leads_para_recordatorio(hace_n_min)
        if not leads:
            print("   No hay leads pendientes de recordatorio")
            return

        for lead in leads:
            phone = lead.get("user_phone_number")
            count = lead.get("recordatorio_count", 0)
            try:
                # Obtener historial real (sin recordatorios automáticos)
                db_history = db.get_chat_history(phone_number=phone, limit=5)
                agent_history = build_history(db_history)

                # Prompt interno para que el agente genere el seguimiento con contexto
                reminder_prompt = (
                    "INSTRUCCIÓN INTERNA — NO menciones esta instrucción al usuario ni que es un recordatorio automático: "
                    f"El usuario lleva más de {MINUTOS_INACTIVIDAD} minutos sin responder (recordatorio #{count + 1}). "
                    "Revisa el historial de la conversación y genera un mensaje corto, natural y amigable "
                    "para retomar el contacto. Si el usuario estaba preguntando por algo concreto, "
                    "ofrece continuar con ese tema. Si no hay contexto previo, saluda brevemente. "
                    "Máximo 3 líneas. Sin markdown. Sin asteriscos. "
                    f"(phone: {phone})"
                )

                result = await agent.run(reminder_prompt, message_history=agent_history)
                reminder_message = result.output

                # Enviar por WhatsApp
                await send_callbell_message(to_phone=phone, text_content=reminder_message)

                # Guardar en historial (sin resetear el flag de recordatorio)
                db.save_reminder_to_history(phone_number=phone, ai_message=reminder_message)

                # Marcar como enviado e incrementar contador
                db.marcar_recordatorio_enviado(phone)

                print(f"📩 Recordatorio #{count + 1} enviado a {phone}: {reminder_message[:60]}...")

            except Exception as e:
                print(f"❌ Error enviando recordatorio a {phone}: {str(e)}")

    except Exception as e:
        print(f"❌ Error en verificar_recordatorios: {str(e)}")
