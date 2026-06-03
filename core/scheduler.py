import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from core.db import DB
from core.callbell import send_callbell_message

# Minutos de inactividad antes de enviar el primer recordatorio
MINUTOS_INACTIVIDAD = 3


def start_scheduler(db: DB):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        verificar_recordatorios,
        "interval",
        minutes=1,
        args=[db],
        id="recordatorios",
        misfire_grace_time=30,  # Si el job se atrasa hasta 30s, igual lo ejecuta
    )
    scheduler.start()
    print("⏰ Scheduler de recordatorios iniciado")
    return scheduler


async def verificar_recordatorios(db: DB):
    # Import dentro de la función para evitar imports circulares al iniciar el módulo
    from agents import agent
    from modules.tools import history as build_history

    print("🔍 Verificando leads para recordatorio...")
    hace_n_min = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=MINUTOS_INACTIVIDAD)
    ).isoformat()

    # Evitar mandar dos recordatorios en menos de MINUTOS_INACTIVIDAD minutos
    hace_n_min_recordatorio = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=MINUTOS_INACTIVIDAD)
    ).isoformat()

    try:
        leads_raw = db.get_leads_para_recordatorio(hace_n_min)
        # Filtrar leads que ya recibieron un recordatorio muy reciente
        leads = [
            lead for lead in leads_raw
            if not lead.get("ultimo_recordatorio")
            or lead["ultimo_recordatorio"] < hace_n_min_recordatorio
        ]
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
