import datetime
from supabase import create_client, Client
from postgrest.exceptions import APIError

MAX_RECORDATORIOS = 2  # Máximo de recordatorios por sesión antes de dejar de molestar


class DB:
    def __init__(self, url: str, key: str):
        self.supabase: Client = create_client(url, key)
        self.table_name = "leads"

    def create_new_lead(self, phone_number: str, status: str = "onboarding") -> dict:
        new_lead = {
            "user_phone_number": phone_number,
            "status": status,
            "conversation": [],
            "ultimo_mensaje": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "recordatorio_enviado": False,
            "recordatorio_count": 0,
        }
        try:
            result = self.supabase.table(self.table_name).insert(new_lead).execute()
            print(f"✅ new lead inserted: {phone_number}")
            return result.data[0]
        except APIError as e:
            if e.code == "23505" or "unique" in str(e).lower():
                print(f"⚠️ lead with {phone_number} already exist. getting data...")
                return self.get_lead(phone_number)
            else:
                raise e

    def get_lead(self, phone_number: str) -> dict | None:
        result = (
            self.supabase.table(self.table_name)
            .select("*")
            .eq("user_phone_number", phone_number)
            .execute()
        )
        return result.data[0] if result.data else None

    def update_status(self, phone_number: str, status: str) -> dict:
        result = (
            self.supabase.table(self.table_name)
            .update({"status": status})
            .eq("user_phone_number", phone_number)
            .execute()
        )
        return result.data

    def update_history_message(self, phone_number: str, user_message: str, ai_message: str) -> dict:
        user = self.get_lead(phone_number)
        if not user:
            raise ValueError(f"lead not found with: {phone_number}")
        actual_history_message = user.get("conversation")
        if not isinstance(actual_history_message, list):
            actual_history_message = []
        new_messages = {
            "user_message": user_message,
            "ai_message": ai_message,
            "date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        actual_history_message.append(new_messages)

        # Si el lead estaba inactive y volvió a escribir, lo reactivamos
        current_status = user.get("status", "onboarding")
        new_status = "onboarding" if current_status == "inactive" else current_status

        result = (
            self.supabase.table(self.table_name)
            .update(
                {
                    "conversation": actual_history_message,
                    "status": new_status,
                    "ultimo_mensaje": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    # Resetea flags cuando el usuario escribe activamente
                    "recordatorio_enviado": False,
                    "recordatorio_count": 0,
                }
            )
            .eq("user_phone_number", phone_number)
            .execute()
        )
        return result.data

    def save_reminder_to_history(self, phone_number: str, ai_message: str) -> dict:
        """
        Guarda el mensaje de recordatorio en el historial SIN resetear recordatorio_enviado.
        También actualiza ultimo_mensaje para que el scheduler no vuelva a disparar
        inmediatamente en el próximo ciclo.
        """
        user = self.get_lead(phone_number)
        if not user:
            raise ValueError(f"lead not found with: {phone_number}")
        actual_history = user.get("conversation")
        if not isinstance(actual_history, list):
            actual_history = []
        actual_history.append(
            {
                "user_message": "[RECORDATORIO AUTOMÁTICO]",
                "ai_message": ai_message,
                "date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        )
        result = (
            self.supabase.table(self.table_name)
            .update(
                {
                    "conversation": actual_history,
                    # Actualiza ultimo_mensaje para evitar re-trigger inmediato
                    "ultimo_mensaje": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
            )
            .eq("user_phone_number", phone_number)
            .execute()
        )
        return result.data

    def get_chat_history(self, phone_number: str, limit: int = 10):
        try:
            response = (
                self.supabase.table("leads")
                .select("conversation")
                .eq("user_phone_number", phone_number)
                .maybe_single()
                .execute()
            )
            if response.data and "conversation" in response.data:
                history = response.data["conversation"]
                # Filtra entradas de recordatorios automáticos para no confundir al agente
                clean_history = [
                    msg for msg in history
                    if msg.get("user_message") != "[RECORDATORIO AUTOMÁTICO]"
                ]
                return clean_history[-limit:] if clean_history else []
            return []
        except Exception as e:
            print(f"⚠️ Error al obtener el historial jsonb: {str(e)}")
            return []

    def reset_lead(self, phone_number: str) -> dict:
        result = (
            self.supabase.table(self.table_name)
            .update(
                {
                    "status": "onboarding",
                    "conversation": [],
                    "ultimo_mensaje": None,
                    "recordatorio_enviado": False,
                    "recordatorio_count": 0,
                }
            )
            .eq("user_phone_number", phone_number)
            .execute()
        )
        print(f"🔄 Lead reseteado a onboarding: {phone_number}")
        return result.data

    def get_leads_para_recordatorio(self, antes_de: str) -> list:
        """
        Obtiene leads en onboarding que:
        - Su último mensaje real fue antes del timestamp dado
        - No han superado el límite de recordatorios por sesión
        """
        try:
            result = (
                self.supabase.table(self.table_name)
                .select("user_phone_number, ultimo_mensaje, recordatorio_count")
                .eq("status", "onboarding")
                .lt("ultimo_mensaje", antes_de)
                .lt("recordatorio_count", MAX_RECORDATORIOS)
                .not_.is_("ultimo_mensaje", "null")
                .execute()
            )
            return result.data if result.data else []
        except Exception as e:
            print(f"⚠️ Error obteniendo leads para recordatorio: {str(e)}")
            return []

    def set_inactive(self, phone_number: str) -> dict:
        """
        Marca el lead como 'inactive': el usuario ya obtuvo lo que quería y se retiró.
        El scheduler NO enviará recordatorios a leads con este status.
        Se resetea a 'onboarding' si el usuario vuelve a escribir.
        """
        result = (
            self.supabase.table(self.table_name)
            .update({
                "status": "inactive",
                "recordatorio_enviado": True,  # Previene cualquier recordatorio pendiente
                "recordatorio_count": MAX_RECORDATORIOS,  # Lleva el contador al máximo
            })
            .eq("user_phone_number", phone_number)
            .execute()
        )
        print(f"😴 Lead marcado como inactive: {phone_number}")
        return result.data

    def marcar_recordatorio_enviado(self, phone_number: str) -> dict:
        """Incrementa el contador de recordatorios."""
        lead = self.get_lead(phone_number)
        current_count = lead.get("recordatorio_count", 0) if lead else 0
        result = (
            self.supabase.table(self.table_name)
            .update({"recordatorio_count": current_count + 1})
            .eq("user_phone_number", phone_number)
            .execute()
        )
        return result.data
