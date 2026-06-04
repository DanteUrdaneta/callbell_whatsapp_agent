"""
drive_reader.py
Lee todos los PDFs de una carpeta de Google Drive y extrae su texto.
Se ejecuta al iniciar el servidor y se refresca cada REFRESH_HOURS horas.
"""

import os
import io
import json
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

FOLDER_ID = "1z2HYoD_sNI9Iyh29Y1E_uw4GwY9K8Bb1"
REFRESH_HOURS = 6

_cotizaciones_cache: Optional[str] = None


def _get_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not creds_json:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON no está configurado")

        creds_info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        return build("drive", "v3", credentials=credentials)
    except Exception as e:
        print(f"❌ Error creando cliente de Drive: {e}")
        raise


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    try:
        import pypdf
        import re
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            # Limpiar espacios dobles que genera pypdf en PDFs escaneados
            text = re.sub(r'  +', ' ', text)        # múltiples espacios → uno
            text = re.sub(r' \n', '\n', text)        # espacio antes de salto → salto
            text = re.sub(r'\n{3,}', '\n\n', text)  # más de 2 saltos → 2
            pages.append(text.strip())
        return "\n\n".join(pages).strip()
    except Exception as e:
        print(f"❌ Error extrayendo texto del PDF: {e}")
        return ""


def load_cotizaciones() -> str:
    global _cotizaciones_cache

    print("📂 Cargando cotizaciones desde Google Drive...")
    try:
        service = _get_drive_service()

        results = (
            service.files()
            .list(
                q=f"'{FOLDER_ID}' in parents and mimeType='application/pdf' and trashed=false",
                fields="files(id, name)",
                orderBy="name",
            )
            .execute()
        )

        files = results.get("files", [])
        if not files:
            print("⚠️ No se encontraron PDFs en la carpeta de Drive")
            return ""

        print(f"📄 Encontrados {len(files)} PDFs: {[f['name'] for f in files]}")

        all_text = []
        for file in files:
            try:
                request = service.files().get_media(fileId=file["id"])
                pdf_bytes = request.execute()
                text = _extract_text_from_pdf(pdf_bytes)
                if text:
                    all_text.append(f"=== {file['name']} ===\n{text}")
                    print(f"✅ PDF leído: {file['name']} ({len(text)} chars)")
                else:
                    print(f"⚠️ PDF sin texto extraíble: {file['name']}")
            except Exception as e:
                print(f"❌ Error leyendo {file['name']}: {e}")
                continue

        _cotizaciones_cache = "\n\n".join(all_text)
        print(f"📚 Cotizaciones cargadas: {len(_cotizaciones_cache)} caracteres totales")
        return _cotizaciones_cache

    except Exception as e:
        print(f"❌ Error cargando cotizaciones de Drive: {e}")
        return _cotizaciones_cache or ""


def get_cotizaciones() -> str:
    global _cotizaciones_cache
    if _cotizaciones_cache is None:
        return load_cotizaciones()
    return _cotizaciones_cache
