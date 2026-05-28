import asyncio
import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from core.db import DB
from core.callbell import send_callbell_message

RECORDATORIO_MENSAJE = (
    "Hola, hace un momento estuvimos en contacto sobre los cursos de ENALAS. "
    "¿Tienes alguna pregunta o te gustaría recibir más información? "
    "Estoy aquí para ayudarte."
)

def start_scheduler(db: DB):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        verificar_recordatorios,
        "interval",
        minutes=1,
        args=[db],
        id="recordatorios"
    )
    scheduler.start()
    print("⏰ Scheduler de recordatorios iniciado")
    return scheduler

async def verificar_recordatorios(db: DB):
    print("🔍 Verificando leads para recordatorio...")
    hace_3min = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=3)
    ).isoformat()
    try:
        leads = db.get_leads_para_recordatorio(hace_3min)
        if not leads:
            print("   No hay leads pendientes de recordatorio")
            return
        for lead in leads:
            phone = lead.get("user_phone_number")
            try:
                await send_callbell_message(to_phone=phone, text_content=RECORDATORIO_MENSAJE)
                db.marcar_recordatorio_enviado(phone)
                print(f"📩 Recordatorio enviado a: {phone}")
            except Exception as e:
                print(f"❌ Error enviando recordatorio a {phone}: {str(e)}")
    except Exception as e:
        print(f"❌ Error en verificar_recordatorios: {str(e)}")
