import datetime
from supabase import create_client, Client
from postgrest.exceptions import APIError

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
            "recordatorio_enviado": False
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
            "date": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        actual_history_message.append(new_messages)
        result = (
            self.supabase.table(self.table_name)
            .update({
                "conversation": actual_history_message,
                "ultimo_mensaje": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "recordatorio_enviado": False
            })
            .eq("user_phone_number", phone_number)
            .execute()
        )
        return result.data

    def get_chat_history(self, phone_number: str, limit: int = 10):
        try:
            response = self.supabase.table("leads") \
                .select("conversation") \
                .eq("user_phone_number", phone_number) \
                .maybe_single() \
                .execute()
            if response.data and "conversation" in response.data:
                history = response.data["conversation"]
                return history[-limit:] if history else []
            return []
        except Exception as e:
            print(f"⚠️ Error al obtener el historial jsonb: {str(e)}")
            return []

    def reset_lead(self, phone_number: str) -> dict:
        result = (
            self.supabase.table(self.table_name)
            .update({
                "status": "onboarding",
                "conversation": [],
                "ultimo_mensaje": None,
                "recordatorio_enviado": False
            })
            .eq("user_phone_number", phone_number)
            .execute()
        )
        print(f"🔄 Lead reseteado a onboarding: {phone_number}")
        return result.data

    def get_leads_para_recordatorio(self) -> list:
        """Obtiene leads en onboarding sin recordatorio enviado y con último mensaje hace más de 24 horas."""
        hace_24h = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
        ).isoformat()
        try:
            result = (
                self.supabase.table(self.table_name)
                .select("user_phone_number, ultimo_mensaje")
                .eq("status", "onboarding")
                .eq("recordatorio_enviado", False)
                .lt("ultimo_mensaje", hace_24h)
                .not_.is_("ultimo_mensaje", "null")
                .execute()
            )
            return result.data if result.data else []
        except Exception as e:
            print(f"⚠️ Error obteniendo leads para recordatorio: {str(e)}")
            return []

    def marcar_recordatorio_enviado(self, phone_number: str) -> dict:
        result = (
            self.supabase.table(self.table_name)
            .update({"recordatorio_enviado": True})
            .eq("user_phone_number", phone_number)
            .execute()
        )
        return result.data
