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


async def send_callbell_document(to_phone: str, file_url: str, filename: str, file_id: str = None):
    """Descarga el PDF autenticado desde Drive y lo envía por Callbell con el nombre correcto."""
    import asyncio
    callbell_url = "https://api.callbell.eu/v1/messages/send"

    try:
        # 1. Descargar el PDF usando el service account (autenticado, sin redirect de confirmación)
        pdf_bytes = await asyncio.get_event_loop().run_in_executor(
            None, _download_pdf_from_drive, file_id or file_url
        )
        if not pdf_bytes:
            print(f"❌ No se pudo descargar el PDF")
            return None

        # 2. Subir a 0x0.st para obtener URL pública con nombre correcto
        async with httpx.AsyncClient(timeout=60) as client:
            upload_response = await client.post(
                "https://0x0.st",
                files={"file": (filename, pdf_bytes, "application/pdf")},
            )
            if upload_response.status_code != 200:
                print(f"❌ Error subiendo a 0x0.st: {upload_response.status_code} - {upload_response.text}")
                return None
            public_url = upload_response.text.strip()
            print(f"📤 PDF subido: {public_url}")

            # 3. Enviar a Callbell
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


def _download_pdf_from_drive(file_id_or_url: str) -> bytes | None:
    """Descarga un PDF de Drive usando el service account."""
    import os, json
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        # Extraer file_id si es una URL
        if file_id_or_url.startswith("http"):
            import urllib.parse
            parsed = urllib.parse.urlparse(file_id_or_url)
            params = urllib.parse.parse_qs(parsed.query)
            file_id = params.get("id", [None])[0]
        else:
            file_id = file_id_or_url

        if not file_id:
            return None

        creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not creds_json:
            return None

        creds_info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/drive"]
        )
        service = build("drive", "v3", credentials=credentials)
        request = service.files().get_media(fileId=file_id)
        return request.execute()
    except Exception as e:
        print(f"❌ Error descargando de Drive: {e}")
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
