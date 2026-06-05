"""
dashboard.py
Actualiza la tabla Dashboard en Airtable con métricas en tiempo real
obtenidas desde Supabase.
"""

import os
from dotenv import load_dotenv

load_dotenv()

AIRTABLE_ACCESS_TOKEN   = os.environ.get("AIRTABLE_ACCESS_TOKEN")
AIRTABLE_DASHBOARD_BASE = os.environ.get("AIRTABLE_DASHBOARD_BASE_ID")
DASHBOARD_TABLE_NAME    = "Dashboard"


def update_dashboard(db) -> None:
    """
    Lee métricas de Supabase y actualiza (o crea) el único record del Dashboard.
    Se llama después de cada mensaje procesado.
    """
    try:
        from pyairtable import Api

        # ── Obtener métricas desde Supabase ──────────────────────────────
        result = db.supabase.table(db.table_name).select(
            "status, tokens_used, conversation"
        ).execute()

        rows = result.data or []

        total          = len(rows)
        activos        = sum(1 for r in rows if r.get("status") == "onboarding")
        exitosos       = sum(1 for r in rows if r.get("status") == "success")
        tokens_totales = sum((r.get("tokens_used") or 0) for r in rows)

        # ── Conectar a Airtable ───────────────────────────────────────────
        api   = Api(AIRTABLE_ACCESS_TOKEN)
        table = api.table(AIRTABLE_DASHBOARD_BASE, DASHBOARD_TABLE_NAME)

        # ── Leer nombres reales de campos desde el schema ─────────────────
        schema       = table.schema()
        field_names  = [f.name for f in schema.fields]
        print(f"🔍 Campos reales en Airtable: {field_names}")

        # Mapeo flexible: busca el campo por palabras clave (case-insensitive)
        def find_field(keywords):
            for name in field_names:
                name_lower = name.lower()
                if all(k in name_lower for k in keywords):
                    return name
            return None

        campo_activos    = find_field(["activos"])        or "numero de usuarios activos"
        campo_exitosos   = find_field(["exitosos"])       or "numero de usuarios exitosos"
        campo_total      = find_field(["conversaciones"]) or "conversaciones totales"
        campo_tokens     = find_field(["tokens"])         or "tokens totales"

        print(f"📌 Usando campos: '{campo_activos}' | '{campo_exitosos}' | '{campo_total}' | '{campo_tokens}'")

        fields = {
            campo_activos:  activos,
            campo_exitosos: exitosos,
            campo_total:    total,
            campo_tokens:   tokens_totales,
        }

        # ── Actualizar o crear el record ──────────────────────────────────
        records = table.all()
        if records:
            table.update(records[0]["id"], fields)
        else:
            table.create(fields)

        print(f"📊 Dashboard actualizado: activos={activos}, exitosos={exitosos}, total={total}, tokens={tokens_totales}")

    except Exception as e:
        print(f"⚠️ Error actualizando dashboard: {e}")
