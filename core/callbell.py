import os
import httpx

CALLBELL_API_KEY = os.environ.get("CALLBELL_API_KEY")

async def send_callbell_message(to_phone: str, text_content: str):
    url = "https://api.callbell.eu/v1/messages/send"
    
    headers = {
            "Authorization": f"Bearer {CALLBELL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "to": to_phone,
        "from": "whatsapp",
        "type": "text",
        "content": {
            "text": text_content
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                print(f"�� Message successfully sent to {to_phone}")
                return response.json()
            else:
                print(f"❌ Failed to send Callbell message: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            print(f"�� HTTP Error sending Callbell message: {str(e)}")
            return None
     

async def pause_callbell_chat(contact_uuid: str):
    url = f"https://api.callbell.eu/v1/contacts/{contact_uuid}"
    headers = {
            "Authorization": f"Bearer {CALLBELL_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
            "team_uuid": CALLBELL_TEAM_UUID,
        "bot_status": "paused"
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.patch(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                print(f"⏸️ chat paused for lead: {contact_uuid}")
                return response.json()
            else:
                print(f"❌ Error on pause lead chat: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            print(f"�� HTTP Error pause lead chat: {str(e)}")
            return None
