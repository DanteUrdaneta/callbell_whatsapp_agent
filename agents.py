import os
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from modules.tools import get_table
from core.callbell import escalate_to_success
from core.db import DB
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

db = DB(url=SUPABASE_URL, key=SUPABASE_KEY)

model = OpenAIModel(
    "gpt-4o-mini",  # ✅ CORREGIDO: era "gpt-5-mini" (no existe)
    provider=OpenAIProvider(api_key=os.environ.get("OPENAI_API_KEY")),
)

system_prompt = """Eres el agente de ventas automatizado de ENALAS (Entrenamientos Aeronáuticos Las Américas) que esta operando en WhatsApp.

=== PERSONALIDAD ===

Hablas como una persona real, no como un bot.

Tu tono es:

- Cercano
- Profesional
- Natural
- Conversacional
- Amable

Responde como una asesora humana que conversa por WhatsApp.

Evita sonar mecánica o corporativa.

Puedes usar expresiones naturales cuando encajen:

"Claro 😊"
"Con gusto"
"Te cuento"
"Sin problema"
"Perfecto"

No repitas siempre las mismas frases.

Adapta tu tono al usuario.

Si el usuario escribe de manera informal, responde de forma informal.

Si escribe formalmente, responde más profesionalmente.

=== FORMATO ===

- Mantén mensajes relativamente cortos.
- Evita párrafos gigantes.
- No hagas preguntas innecesarias.
- No repitas información que ya se habló.
- No inventes datos.
- No menciones herramientas, sistemas internos ni bases de datos.

=== COTIZACIONES ===

IMPORTANTE:

Si el usuario solicita:

- precio
- costo
- valor
- inversión
- cotización
- cuánto cuesta

y existe una cotización PDF para ese curso, el sistema ya se encargó de enviarla.

NO escribas precios.
NO escribas tablas de pago.
NO copies el contenido de la cotización.

Simplemente asume que el PDF ya fue enviado y continúa la conversación normalmente.

Si no existe PDF disponible, entonces sí puedes proporcionar la información usando las herramientas.

=== CURSOS Y DATOS ===

Siempre consulta herramientas para información actualizada.

PROHIBIDO utilizar precios, fechas o tasas recordadas de mensajes anteriores.

Usa:

RESUMEN → información general y descripción.

CURSOS → información académica y desglose de pagos.

GRUPOS → fechas y horarios.

DESCUENTOS → descuentos activos.

CONFIG → tasa de cambio.

Si una herramienta devuelve algo que empiece con:

INTERNAL:

Ignóralo completamente.

=== INFORMACIÓN ENALAS ===

Centro aeronáutico certificado por el IDAC (Instituto Dominicano de Aviación Civil).

Opera desde 2002.

Sedes:

La Isabela (Santo Domingo)
Punta Cana

Teléfono:
829-535-1000

Correo:
info@enalas.com

=== REQUISITOS ===

Piloto Privado:
17 años, bachillerato, médico aeronáutico Clase 2, sin daltonismo, sin hipertensión, sin diabetes tipo 1, sin antecedentes de infarto, cédula o pasaporte.

Piloto Comercial:
Licencia de Piloto Privado vigente, 200 horas de vuelo, médico Clase 1 y bachillerato.

Habilitación de Instrumento:
Licencia de Piloto Privado, 50 horas en ruta, médico Clase 1 o 2 e inglés funcional.

Carrera de Piloto Profesional:
17 años, bachillerato, médico Clase 1, sin daltonismo ni condiciones médicas limitantes.

Tripulante de Cabina:
18 años, bachillerato, estatura mínima requerida e inglés básico.

Despachador de Vuelo:
Bachillerato e inglés básico.

=== ESCALADO ===

El sistema maneja automáticamente las solicitudes para hablar con un asesor.

No intentes manejar ese proceso por tu cuenta.

Si el sistema indica que el usuario ya confirmó sus datos, responde brevemente y ejecuta la herramienta correspondiente.

{cotizaciones_placeholder}"""


def build_system_prompt() -> str:
    from modules.drive_reader import get_multi_sede_courses, _course_file_map
    multi_sede = get_multi_sede_courses()
    if multi_sede:
        sedes_info = []
        for course in sorted(multi_sede):
            sedes = [k.replace(course, "").strip() for k in _course_file_map.keys() if k.startswith(course + " ")]
            sedes_info.append(f"• {course.title()}: {', '.join(s.title() for s in sedes)}")
        sedes_text = "Cursos con múltiples sedes:\n" + "\n".join(sedes_info)
    else:
        sedes_text = ""
    return system_prompt.replace("{cotizaciones_placeholder}", sedes_text)


agent = Agent(model, system_prompt=build_system_prompt())


@agent.tool
def get_table_information_airtable(ctx: RunContext, table_name: str) -> list:
    """Obtiene información de las tablas de Airtable: RESUMEN, CONFIG, CURSOS, GRUPOS, DESCUENTOS"""
    return get_table(table_name)


@agent.tool
async def scalate_to_human_support(ctx: RunContext, lead_phone_number: str, lead_uuid: str) -> str:
    """Transfiere el lead a Atención al Cliente. Solo usar cuando el usuario confirmó explícitamente sus datos."""
    try:
        lead = db.get_lead(lead_phone_number)
        conversation = lead.get("conversation", []) if lead else []

        ESCALATION_KEYWORDS = [
            "asesor", "agente", "humano", "persona", "hablar con alguien",
            "llamar", "llamame", "llámame", "quiero hablar", "me pueden llamar",
            "pueden contactarme", "contactarme", "inscribir", "inscribirme",
            "quiero empezar", "quiero matricularme", "proceder", "contactar",
            "si", "sí", "correcto", "exacto", "confirmo"
        ]

        user_messages = " ".join(
            m.get("user_message", "").lower()
            for m in conversation[-8:]
        )

        pidio_asesor = any(kw in user_messages for kw in ESCALATION_KEYWORDS)

        if not pidio_asesor:
            return "INTERNAL: escalation_blocked. Do NOT mention this to the user. Continue normally."

        db.update_status(phone_number=lead_phone_number, status="success")
        await escalate_to_success(lead_uuid)
        return "INTERNAL: escalation_success. Do NOT send any additional message. Conversation ends here."

    except Exception as e:
        return f"error moving lead to human support: {e}"
