import os
from dotenv import load_dotenv

load_dotenv()

AIRTABLE_ACCESS_TOKEN   = os.environ.get("AIRTABLE_ACCESS_TOKEN")
AIRTABLE_DASHBOARD_BASE = os.environ.get("AIRTABLE_DASHBOARD_BASE_ID")
DASHBOARD_TABLE_NAME    = "Dashboard"

# Nombres exactos de los campos en Airtable (deben coincidir al 100%)
FIELD_ACTIVOS    = "numero de usuarios activos"
FIELD_EXITOSOS   = "numero de usuarios exitosos"
FIELD_TOTAL      = "conversaciones totales"
FIELD_TOKENS     = "tokens totales"


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

        print(f"📊 Métricas calculadas: activos={activos}, exitosos={exitosos}, total={total}, tokens={tokens_totales}")

        # ── Conectar a Airtable ───────────────────────────────────────────
        api   = Api(AIRTABLE_ACCESS_TOKEN)
        table = api.table(AIRTABLE_DASHBOARD_BASE, DASHBOARD_TABLE_NAME)

        fields = {
            FIELD_ACTIVOS:  activos,
            FIELD_EXITOSOS: exitosos,
            FIELD_TOTAL:    total,
            FIELD_TOKENS:   tokens_totales,
        }

        # ── Leer campos reales del primer record para debug ───────────────
        records = table.all()
        if records:
            real_fields = list(records[0].get("fields", {}).keys())
            print(f"🔍 Campos reales en Airtable: {real_fields}")
            table.update(records[0]["id"], fields)
        else:
            print(f"🔍 No hay records, creando uno nuevo...")
            table.create(fields)

        print(f"✅ Dashboard actualizado correctamente")

    except Exception as e:
        print(f"⚠️ Error actualizando dashboard: {e}")
