import os
import io
import json
import logging
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

FOLDER_ID = "1z2HYoD_sNI9Iyh29Y1E_uw4GwY9K8Bb1"
REFRESH_HOURS = 6  # Refrescar cada 6 horas por si actualizan PDFs en Drive

_cotizaciones_cache: Optional[str] = None


def _get_drive_service():
    """Crea el cliente de Google Drive usando las credenciales del env."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        # Las credenciales pueden venir como JSON string en variable de entorno
        creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if creds_json:
            creds_info = json.loads(creds_json)
        else:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON no está configurado")

        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        return build("drive", "v3", credentials=credentials)
    except Exception as e:
        logger.error(f"Error creando cliente de Drive: {e}")
        raise


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extrae texto de un PDF en bytes."""
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    except Exception as e:
        logger.error(f"Error extrayendo texto del PDF: {e}")
        return ""


def load_cotizaciones() -> str:
    """
    Descarga todos los PDFs de la carpeta de Drive y retorna su texto concatenado.
    Guarda en caché para no consultar Drive en cada mensaje.
    """
    global _cotizaciones_cache

    try:
        service = _get_drive_service()

        # Listar todos los PDFs en la carpeta
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
            logger.warning("No se encontraron PDFs en la carpeta de Drive")
            return ""

        logger.info(f"📄 Encontrados {len(files)} PDFs en Drive: {[f['name'] for f in files]}")

        all_text = []
        for file in files:
            try:
                # Descargar el PDF
                request = service.files().get_media(fileId=file["id"])
                pdf_bytes = request.execute()

                text = _extract_text_from_pdf(pdf_bytes)
                if text:
                    all_text.append(f"=== {file['name']} ===\n{text}")
                    logger.info(f"✅ PDF leído: {file['name']} ({len(text)} chars)")
                else:
                    logger.warning(f"⚠️ PDF sin texto extraíble: {file['name']}")
            except Exception as e:
                logger.error(f"Error leyendo {file['name']}: {e}")
                continue

        _cotizaciones_cache = "\n\n".join(all_text)
        logger.info(f"📚 Cotizaciones cargadas: {len(_cotizaciones_cache)} caracteres totales")
        return _cotizaciones_cache

    except Exception as e:
        logger.error(f"Error cargando cotizaciones de Drive: {e}")
        # Si falla, retornar caché anterior si existe
        return _cotizaciones_cache or ""


def get_cotizaciones() -> str:
    """Retorna las cotizaciones del caché. Si no hay caché, las carga."""
    global _cotizaciones_cache
    if _cotizaciones_cache is None:
        return load_cotizaciones()
    return _cotizaciones_cache
