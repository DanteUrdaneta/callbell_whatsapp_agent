"""
dashboard.py
Actualiza la tabla Dashboard en Airtable con métricas en tiempo real
obtenidas desde Supabase.
"""

import os
from dotenv import load_dotenv

load_dotenv()

AIRTABLE_ACCESS_TOKEN    = os.environ.get("AIRTABLE_ACCESS_TOKEN")
AIRTABLE_DASHBOARD_BASE  = os.environ.get("AIRTABLE_DASHBOARD_BASE_ID")
DASHBOARD_TABLE_NAME     = "Dashboard"


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

        # ── Actualizar Airtable ───────────────────────────────────────────
        api   = Api(AIRTABLE_ACCESS_TOKEN)
        table = api.table(AIRTABLE_DASHBOARD_BASE, DASHBOARD_TABLE_NAME)

        # Debug: imprimir campos reales del record
        records = table.all()
        if records:
            print(f"🔍 Campos reales en Airtable: {list(records[0]['fields'].keys())}")

        fields = {
            "numero de usuarios activos":  activos,
            "numero de usuarios exitosos": exitosos,
            "conversaciones totales":      total,
            "tokens totales":              tokens_totales,
        }

        # Buscar el record existente (solo hay uno)
        records = table.all()
        if records:
            table.update(records[0]["id"], fields)
        else:
            table.create(fields)

        print(f"📊 Dashboard actualizado: activos={activos}, exitosos={exitosos}, total={total}, tokens={tokens_totales}")

    except Exception as e:
        print(f"⚠️ Error actualizando dashboard: {e}")
