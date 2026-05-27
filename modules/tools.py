import os
from pyairtable import Api
from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from dotenv import load_dotenv

load_dotenv()

# ── Credenciales desde .env ──────────────────────────────
AIRTABLE_ACCESS_TOKEN = os.environ.get("AIRTABLE_ACCESS_TOKEN")
AIRTABLE_BASE_ID      = os.environ.get("AIRTABLE_BASE_ID")

VALID_TABLES = ["RESUMEN", "CONFIG", "CURSOS", "GRUPOS", "DESCUENTOS"]

def get_table(table_name: str) -> list[dict]:
    """
    Obtiene todos los registros de una tabla de Airtable.
    table_name debe ser uno de: RESUMEN, CONFIG, CURSOS, GRUPOS, DESCUENTOS
    """
    if not AIRTABLE_ACCESS_TOKEN or not AIRTABLE_BASE_ID:
        raise ValueError(
            "❌ AIRTABLE_ACCESS_TOKEN o AIRTABLE_BASE_ID no están definidos en el .env"
        )

    table_name = table_name.strip().upper()
    if table_name not in VALID_TABLES:
        return [{"error": f"Tabla '{table_name}' no válida. Usa una de: {VALID_TABLES}"}]

    print(f"📋 Consultando tabla Airtable: {table_name}")
    api     = Api(AIRTABLE_ACCESS_TOKEN)
    table   = api.table(AIRTABLE_BASE_ID, table_name)
    records = table.all()

    # Devuelve solo los fields para que el agente los procese fácil
    return [r["fields"] for r in records]


def history(db_history: list) -> list[ModelMessage]:
    """Convierte el historial de Supabase al formato que espera pydantic-ai."""
    agent_history: list[ModelMessage] = []

    for msg in db_history:
        agent_history.append(
            ModelRequest(parts=[UserPromptPart(content=msg["user_message"])])
        )
        agent_history.append(
            ModelResponse(parts=[TextPart(content=msg["ai_message"])])
        )

    return agent_history
