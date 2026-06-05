import os
import io
import json
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

FOLDER_ID = "1z2HYoD_sNI9Iyh29Y1E_uw4GwY9K8Bb1"
REFRESH_HOURS = 6

_cotizaciones_cache: Optional[str] = None
# Cache de metadatos: {nombre_archivo: file_id}
_files_metadata: dict = {}

# Palabras clave para detectar qué curso pide el usuario
COURSE_KEYWORDS = {
    "piloto privado punta cana": ["privado", "punta cana", "cppa"],
    "piloto privado santo domingo": ["privado", "santo domingo", "cpp"],
    "piloto comercial": ["comercial", "cpc"],
    "tripulante de cabina": ["tripulante", "cabina", "azafata", "auxiliar"],
    "despachador": ["despachador", "despacho"],
    "habilitacion instrumento": ["instrumento", "chi", "habilitacion"],
    "carrera piloto profesional": ["carrera", "profesional", "monomotor"],
}

# Mapeo de curso a nombre de archivo (palabras clave del nombre)
COURSE_FILE_KEYWORDS = {
    "piloto privado punta cana": "PUNTA CANA",
    "piloto privado santo domingo": "Piloto Privado (ENLS-1-CPP)",
    "piloto comercial": "Piloto Comercial",
    "tripulante de cabina": "Tripulante",
    "despachador": "DESPACHADOR",
    "habilitacion instrumento": "Habilitacion de Instrumento",
    "carrera piloto profesional": "CARRERA PILOTO",
}


def get_pdf_url_for_course(course_key: str) -> tuple[str, str, str] | None:
    """
    Retorna (url_descarga, nombre_archivo, file_id) para el curso dado.
    """
    if not _files_metadata:
        return None

    file_keyword = COURSE_FILE_KEYWORDS.get(course_key, "").upper()
    for filename, file_id in _files_metadata.items():
        if file_keyword and file_keyword.upper() in filename.upper():
            url = f"https://drive.google.com/uc?export=download&id={file_id}"
            return url, filename, file_id

    return None


def detect_course_from_message(message: str) -> str | None:
    """Detecta qué curso está pidiendo el usuario basado en palabras clave."""
    msg_lower = message.lower()
    for course_key, keywords in COURSE_KEYWORDS.items():
        if all(kw in msg_lower for kw in keywords) or any(kw in msg_lower for kw in keywords[:1]):
            # Verificar que hay al menos la primera keyword (más específica)
            if keywords[0] in msg_lower:
                return course_key
    return None


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
            scopes=["https://www.googleapis.com/auth/drive"],
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


def make_files_public():
    """Hace que todos los PDFs de la carpeta sean accesibles públicamente por URL."""
    try:
        service = _get_drive_service()
        for filename, file_id in _files_metadata.items():
            try:
                service.permissions().create(
                    fileId=file_id,
                    body={"type": "anyone", "role": "reader"},
                ).execute()
            except Exception:
                pass  # Ya puede estar público
        print("🌐 PDFs configurados como públicos en Drive")
    except Exception as e:
        print(f"⚠️ Error configurando permisos públicos: {e}")


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
            # Guardar metadata para URLs de descarga
            _files_metadata[file["name"]] = file["id"]
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
        make_files_public()
        return _cotizaciones_cache

    except Exception as e:
        print(f"❌ Error cargando cotizaciones de Drive: {e}")
        return _cotizaciones_cache or ""


def get_cotizaciones() -> str:
    global _cotizaciones_cache
    if _cotizaciones_cache is None:
        return load_cotizaciones()
    return _cotizaciones_cache


def _download_pdf_from_drive_by_id(file_id: str) -> bytes | None:
    """Descarga un PDF de Drive por file_id usando el service account."""
    try:
        service = _get_drive_service()
        request = service.files().get_media(fileId=file_id)
        return request.execute()
    except Exception as e:
        print(f"❌ Error descargando PDF {file_id}: {e}")
        return None
