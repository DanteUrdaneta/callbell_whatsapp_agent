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

        print(f"📊 Métricas calculadas: activos={activos}, exitosos={exitosos}, total={total}, tokens={tokens_totales}")

        # ── Conectar a Airtable ───────────────────────────────────────────
        api   = Api(AIRTABLE_ACCESS_TOKEN)
        table = api.table(AIRTABLE_DASHBOARD_BASE, DASHBOARD_TABLE_NAME)

        # ── Leer campos reales y mapear dinámicamente ─────────────────────
        records = table.all()
        if not records:
            print(f"⚠️ No hay records en Dashboard, no se puede actualizar")
            return

        real_fields = list(records[0].get("fields", {}).keys())
        print(f"🔍 Campos reales en Airtable: {real_fields}")

        # Buscar cada campo limpiando BOM y espacios extra
        def find_field(keyword):
            for name in real_fields:
                clean = name.replace("\ufeff", "").strip()
                if keyword.lower() in clean.lower():
                    return name  # retorna el nombre ORIGINAL con BOM si lo tiene
            return None

        campo_activos  = find_field("activos")
        campo_exitosos = find_field("exitosos")
        campo_total    = find_field("conversaciones")
        campo_tokens   = find_field("tokens")

        print(f"📌 Campos mapeados: {campo_activos} | {campo_exitosos} | {campo_total} | {campo_tokens}")

        if not all([campo_activos, campo_exitosos, campo_total, campo_tokens]):
            print(f"⚠️ No se encontraron todos los campos. Disponibles: {real_fields}")
            return

        fields = {
            campo_activos:  activos,
            campo_exitosos: exitosos,
            campo_total:    total,
            campo_tokens:   tokens_totales,
        }

        table.update(records[0]["id"], fields)
        print(f"✅ Dashboard actualizado correctamente")

    except Exception as e:
        print(f"⚠️ Error actualizando dashboard: {e}")
