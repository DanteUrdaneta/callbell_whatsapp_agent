import os
import httpx

CALLBELL_API_KEY = os.environ.get("CALLBELL_API_KEY")
CALLBELL_CHANNEL_UUID = os.environ.get("CALLBELL_CHANNEL_UUID")
CALLBELL_SUCCESS_TEAM_UUID = os.environ.get("CALLBELL_TEAM_UUID", "832893894e364131b3c4715f5e5b7227")


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
    # FIX: agregado timeout=30 para no colgar el event loop indefinidamente
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                print(f"✅ Message successfully sent to {to_phone}")
                return response.json()
            else:
                print(f"❌ Failed to send Callbell message: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            print(f"❌ HTTP Error sending Callbell message: {str(e)}")
            return None


async def send_callbell_document(to_phone: str, file_url: str, filename: str, file_id: str = None):
    """Envía un documento PDF via Callbell."""
    callbell_url = "https://api.callbell.eu/v1/messages/send"
    headers = {
        "Authorization": f"Bearer {CALLBELL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": to_phone,
        "from": "whatsapp",
        "type": "document",
        "content": {
            "url": file_url,
            "name": filename,
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(callbell_url, headers=headers, json=payload)
            if response.status_code in [200, 201]:
                print(f"✅ Documento enviado a {to_phone}: {filename}")
                return response.json()
            else:
                print(f"❌ Error enviando documento: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            print(f"❌ HTTP Error enviando documento: {str(e)}")
            return None


# FIX: convertida a async para no bloquear el event loop con httpx.Client() síncrono
async def escalate_to_success(contact_uuid: str):
    """Async: asigna al equipo de Atención al Cliente y termina el bot."""
    url = f"https://api.callbell.eu/v1/contacts/{contact_uuid}"
    headers = {
        "Authorization": f"Bearer {CALLBELL_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "team_uuid": CALLBELL_SUCCESS_TEAM_UUID,
        "bot_status": "bot_end"
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                print(f"✅ Lead escalado a Atención al Cliente: {contact_uuid}")
                return response.json()
            else:
                print(f"❌ Error escalando: {response.status_code} - {response.text}")
                return None
    except Exception as e:
        print(f"❌ HTTP Error escalando: {str(e)}")
        return None
