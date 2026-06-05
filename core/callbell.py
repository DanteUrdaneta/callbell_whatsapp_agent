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
    """Descarga el PDF y lo envía como documento con nombre correcto via Callbell."""
    import io
    url = "https://api.callbell.eu/v1/messages/send"
    headers = {
        "Authorization": f"Bearer {CALLBELL_API_KEY}",
    }

    try:
        # Descargar el PDF de Drive
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            pdf_response = await client.get(file_url)
            if pdf_response.status_code != 200:
                print(f"❌ No se pudo descargar el PDF: {pdf_response.status_code}")
                return None
            pdf_bytes = pdf_response.content

        # Enviar como multipart con nombre correcto
        async with httpx.AsyncClient(timeout=30) as client:
            files = {
                "file": (filename, io.BytesIO(pdf_bytes), "application/pdf"),
            }
            data = {
                "to": to_phone,
                "from": "whatsapp",
                "type": "document",
            }
            response = await client.post(url, headers=headers, data=data, files=files)
            if response.status_code in [200, 201]:
                print(f"✅ Documento enviado a {to_phone}: {filename}")
                return response.json()
            else:
                print(f"❌ Error enviando documento: {response.status_code} - {response.text}")
                # Fallback: intentar con URL directa
                payload = {
                    "to": to_phone,
                    "from": "whatsapp",
                    "type": "document",
                    "content": {"url": file_url, "name": filename},
                }
                headers["Content-Type"] = "application/json"
                response2 = await client.post(url, headers=headers, json=payload)
                if response2.status_code in [200, 201]:
                    print(f"✅ Documento enviado (fallback URL) a {to_phone}: {filename}")
                    return response2.json()
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
