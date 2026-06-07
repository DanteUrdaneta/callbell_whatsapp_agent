import os
from pyairtable import Api
from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from dotenv import load_dotenv

load_dotenv()

# ── Credenciales desde .env ──────────────────────────────
AIRTABLE_ACCESS_TOKEN = os.environ.get("AIRTABLE_ACCESS_TOKEN")
AIRTABLE_BASE_ID      = os.environ.get("AIRTABLE_BASE_ID")

VALID_TABLES = ["RESUMEN", "CONFIG", "CURSOS", "GRUPOS", "DESCUENTOS"]

# Campos de Airtable que pueden contener el nombre del curso
COURSE_NAME_FIELDS = ["nombre", "curso", "name", "course", "Nombre", "Curso"]

def _record_matches_sede(fields: dict, sede: str) -> bool:
    """
    Retorna True si el registro pertenece a la sede indicada.
    Busca en todos los campos de texto del registro.
    """
    sede_norm = sede.lower()
    for value in fields.values():
        if isinstance(value, str) and sede_norm in value.lower():
            return True
    return False

def get_table(table_name: str, sede: str | None = None) -> list[dict]:
    """
    Obtiene registros de una tabla de Airtable.
    table_name debe ser uno de: RESUMEN, CONFIG, CURSOS, GRUPOS, DESCUENTOS
    sede: si se especifica (ej. 'punta cana' o 'santo domingo'), filtra los registros
          que contengan esa sede en cualquier campo de texto. Solo aplica a tablas
          con datos por sede (RESUMEN, CURSOS, GRUPOS).
    """
    if not AIRTABLE_ACCESS_TOKEN or not AIRTABLE_BASE_ID:
        raise ValueError(
            "❌ AIRTABLE_ACCESS_TOKEN o AIRTABLE_BASE_ID no están definidos en el .env"
        )

    table_name = table_name.strip().upper()
    if table_name not in VALID_TABLES:
        return [{"error": f"Tabla '{table_name}' no válida. Usa una de: {VALID_TABLES}"}]

    print(f"📋 Consultando tabla Airtable: {table_name}" + (f" (sede: {sede})" if sede else ""))
    api     = Api(AIRTABLE_ACCESS_TOKEN)
    table   = api.table(AIRTABLE_BASE_ID, table_name)
    records = table.all()

    all_fields = [r["fields"] for r in records]

    # Filtrar por sede si se especificó y la tabla lo soporta
    if sede and table_name in ("RESUMEN", "CURSOS", "GRUPOS"):
        filtered = [f for f in all_fields if _record_matches_sede(f, sede)]
        # Si el filtro no devuelve nada (por diferencias en nombres de campo),
        # devolver todos para no romper el flujo
        if filtered:
            print(f"🔍 Filtrado por sede '{sede}': {len(filtered)}/{len(all_fields)} registros")
            return filtered

    return all_fields


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
