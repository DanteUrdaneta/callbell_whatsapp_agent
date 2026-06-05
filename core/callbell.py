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
    async with httpx.AsyncClient() as client:
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


async def send_callbell_document(to_phone: str, file_url: str, filename: str):
    """Descarga el PDF desde Drive y lo re-sube con el nombre correcto antes de enviarlo por Callbell."""
    callbell_url = "https://api.callbell.eu/v1/messages/send"

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            # 1. Descargar el PDF desde Drive
            dl_response = await client.get(file_url)
            if dl_response.status_code != 200:
                print(f"❌ Error descargando PDF desde Drive: {dl_response.status_code}")
                return None
            pdf_bytes = dl_response.content

            # 2. Re-subir a transfer.sh con el nombre correcto
            upload_url = f"https://transfer.sh/{filename}"
            upload_response = await client.put(
                upload_url,
                content=pdf_bytes,
                headers={"Content-Type": "application/pdf", "Max-Days": "1"},
            )
            if upload_response.status_code != 200:
                print(f"❌ Error subiendo a transfer.sh: {upload_response.status_code} - {upload_response.text}")
                return None
            public_url = upload_response.text.strip()
            print(f"📤 PDF subido a transfer.sh: {public_url}")

            # 3. Enviar a Callbell con la URL pública que ya tiene el nombre correcto
            headers = {
                "Authorization": f"Bearer {CALLBELL_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "to": to_phone,
                "from": "whatsapp",
                "type": "document",
                "content": {
                    "url": public_url,
                    "name": filename,
                },
            }
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


def escalate_to_success(contact_uuid: str):
    """Síncrona: asigna al equipo de Atención al Cliente y termina el bot."""
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
        with httpx.Client() as client:
            response = client.patch(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                print(f"✅ Lead escalado a Atención al Cliente: {contact_uuid}")
                return response.json()
            else:
                print(f"❌ Error escalando: {response.status_code} - {response.text}")
                return None
    except Exception as e:
        print(f"❌ HTTP Error escalando: {str(e)}")
        return None
