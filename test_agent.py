"""
test_agent.py — Prueba el agente ENALAS directamente desde la terminal.
No necesita servidor, ni Callbell, ni Supabase corriendo.

Uso:
    python test_agent.py

Comandos especiales durante la sesión:
    /salir    → termina la sesión
    /reset    → limpia el historial en memoria
    /tabla X  → prueba directo la conexión a Airtable (ej: /tabla RESUMEN)
"""

import os
import sys
import asyncio
from dotenv import load_dotenv

load_dotenv()

# ── Verificación rápida de variables de entorno ──────────
REQUIRED_VARS = {
    "OPENAI_API_KEY":        os.environ.get("OPENAI_API_KEY"),
    "AIRTABLE_ACCESS_TOKEN": os.environ.get("AIRTABLE_ACCESS_TOKEN"),
    "AIRTABLE_BASE_ID":      os.environ.get("AIRTABLE_BASE_ID"),
}

missing = [k for k, v in REQUIRED_VARS.items() if not v]
if missing:
    print("\n❌ Faltan variables en tu .env:")
    for var in missing:
        print(f"   • {var}")
    print("\nCopia .env.example a .env y completa los valores.")
    sys.exit(1)

print("✅ Variables de entorno OK")

# ── Opcional: verificar Airtable antes de arrancar ───────
def test_airtable_connection():
    from modules.tools import get_table
    print("\n🔗 Probando conexión a Airtable (tabla CONFIG)...")
    try:
        data = get_table("CONFIG")
        print(f"   ✅ Airtable OK — {len(data)} registro(s) encontrados")
        return True
    except Exception as e:
        print(f"   ❌ Error Airtable: {e}")
        return False

# ── Loop de chat ─────────────────────────────────────────
async def chat_loop():
    from agents import agent
    from modules.tools import history as build_history

    message_history = []   # historial en memoria para esta sesión

    print("\n" + "="*50)
    print("  🛩️  ENALAS — Agente de Prueba")
    print("="*50)
    print("Escribe un mensaje para hablar con el agente.")
    print("Comandos: /salir  /reset  /tabla NOMBRE\n")

    while True:
        try:
            user_input = input("Tú: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Hasta luego.")
            break

        if not user_input:
            continue

        # ── Comandos especiales ──────────────────────────
        if user_input.lower() == "/salir":
            print("👋 Hasta luego.")
            break

        if user_input.lower() == "/reset":
            message_history = []
            print("🔄 Historial limpiado.\n")
            continue

        if user_input.lower().startswith("/tabla "):
            table_name = user_input.split(" ", 1)[1].upper()
            from modules.tools import get_table
            try:
                data = get_table(table_name)
                print(f"\n📋 Tabla '{table_name}' — {len(data)} registros:")
                for row in data[:3]:   # muestra máximo 3 filas como preview
                    print(f"   {row}")
                if len(data) > 3:
                    print(f"   ... y {len(data)-3} más")
            except Exception as e:
                print(f"❌ Error: {e}")
            print()
            continue

        # ── Llamada al agente ────────────────────────────
        print("⏳ Pensando...")
        try:
            result = await agent.run(
                user_input,
                message_history=message_history,
            )
            response_text = result.output
            message_history = list(result.all_messages())  # conserva historial

            print(f"\nAgente: {response_text}\n")

        except Exception as e:
            print(f"\n❌ Error del agente: {e}\n")


if __name__ == "__main__":
    test_airtable_connection()
    asyncio.run(chat_loop())
