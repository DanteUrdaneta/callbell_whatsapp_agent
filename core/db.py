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
            "conversation": []  
        }
        
        try:
            result = self.supabase.table(self.table_name).insert(new_lead).execute()
            print(f"✅ new lead inserted: {phone_number}")
            return result.data[0]
            
        except APIError as e:
        
            if e.code == "23505" or "unique" in str(e).lower():
                print(f"⚠️ lead with {phone_number} already exist. gettin data...")
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
            .update({"conversation": actual_history_message})
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
