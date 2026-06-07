import os
import io
import re
import json
import unicodedata
from typing import Optional
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

FOLDER_ID    = "1z2HYoD_sNI9Iyh29Y1E_uw4GwY9K8Bb1"
REFRESH_HOURS = 6

_cotizaciones_cache: Optional[str] = None
_files_metadata: dict = {}
_pdf_bytes_cache: dict = {}
_course_file_map: dict = {}
_multi_sede: set = set()


def _normalize(text: str) -> str:
    """Quita tildes, pasa a minúsculas y elimina caracteres no alfanuméricos."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Mapeo explícito: si el nombre normalizado del PDF coincide con alguna de estas claves,
# forzar la sede correspondiente. Esto cubre casos donde el PDF no tiene " - Sede" en el nombre.
SEDE_ALIASES: dict[str, tuple[str, str]] = {
    # nombre_normalizado_sin_extension -> (course_key, sede_key)
    "cotizacion de curso piloto privado": ("cotizacion de curso piloto privado", "santo domingo"),
    "cotizacion de curso piloto privado punta cana": ("cotizacion de curso piloto privado", "punta cana"),
}


def _parse_pdf_name(filename: str):
    """
    Dado el nombre de un PDF retorna (course_key, sede_key).
    Ignora prefijos numéricos y códigos entre paréntesis como (ENLS-1-CPP).

    Ejemplos:
      '08 Cotizacion de Curso Piloto Privado - PUNTA CANA.pdf'
        -> ('cotizacion de curso piloto privado', 'punta cana')
      '08 Cotizacion de Curso Piloto Privado.pdf'
        -> ('cotizacion de curso piloto privado', 'santo domingo')  ← alias
      '11 Cotizacion de Curso Piloto Comercial (ENLS-1-CPC).pdf'
        -> ('cotizacion de curso piloto comercial', None)
    """
    name = re.sub(r"^\d+\s+", "", filename.strip())           # quitar prefijo numérico
    name = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"\s*\([^)]*\)", "", name).strip()          # quitar (ENLS-1-CPP) etc.

    # Verificar alias explícito primero (antes de intentar parsear " - ")
    name_norm = _normalize(name)
    if name_norm in SEDE_ALIASES:
        return SEDE_ALIASES[name_norm]

    if " - " in name:
        parts = name.split(" - ", 1)
        return _normalize(parts[0]), _normalize(parts[1])
    else:
        return _normalize(name), None


def _build_catalog(filenames: list[str]):
    """
    Construye _course_file_map y _multi_sede desde la lista de nombres de archivo.
    Se llama cada vez que se recargan los PDFs de Drive.
    """
    global _course_file_map, _multi_sede

    parsed = []
    for fn in filenames:
        course_key, sede_key = _parse_pdf_name(fn)
        parsed.append((course_key, sede_key, fn))

    sedes_por_curso = defaultdict(list)
    for course_key, sede_key, _ in parsed:
        if sede_key:
            sedes_por_curso[course_key].append(sede_key)

    _multi_sede = {k for k, v in sedes_por_curso.items() if len(v) > 1}

    new_map = {}
    for course_key, sede_key, fn in parsed:
        if sede_key:
            new_map[f"{course_key} {sede_key}"] = fn
        else:
            new_map[course_key] = fn

    _course_file_map = new_map
    print(f"📚 Catálogo dinámico: {list(_course_file_map.keys())}")
    print(f"🏙️  Multi-sede: {_multi_sede}")


def get_multi_sede_courses() -> set:
    """Retorna el set de course_keys genéricos con múltiples sedes."""
    return _multi_sede


# Mapeo de términos que usa el usuario -> course_key en _course_file_map
# Las claves del mapa vienen del nombre del PDF (ej: "cotizacion de curso piloto privado")
# pero el usuario dice cosas como "piloto privado", "piloto comercial", etc.
USER_COURSE_ALIASES: dict[str, str] = {
    "piloto privado": "cotizacion de curso piloto privado",
    "piloto comercial": "cotizacion de curso piloto comercial",
    "habilitacion de instrumento": "cotizacion de curso habilitacion de instrumento",
    "habilitación de instrumento": "cotizacion de curso habilitacion de instrumento",
    "instrumento": "cotizacion de curso habilitacion de instrumento",
    "despachador": "cotizacion de curso despachador",
    "tripulante de cabina": "cotizacion de curso tripulante de cabina",
    "tripulante": "cotizacion de curso tripulante de cabina",
    "carrera de piloto": "cotizacion carrera piloto profesional monomotor 2026",
    "piloto profesional": "cotizacion carrera piloto profesional monomotor 2026",
    "carrera piloto": "cotizacion carrera piloto profesional monomotor 2026",
}


def detect_course_from_message(message: str) -> str | None:
    """
    Detecta qué curso está pidiendo el usuario.
    Primero intenta con aliases amigables (lo que el usuario escribe),
    luego con las claves exactas del catálogo de PDFs.
    Si el curso es multi-sede y el mensaje no especifica sede,
    retorna el course_key genérico para que main.py pregunte la sede.
    """
    msg = _normalize(message)

    # 1. Intentar con aliases de usuario (más fiable)
    for alias, course_key in sorted(USER_COURSE_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        alias_norm = _normalize(alias)
        if alias_norm in msg:
            # Verificar si es multi-sede
            for base_course in _multi_sede:
                if course_key == base_course or course_key.startswith(base_course + " "):
                    # Verificar si el mensaje ya incluye una sede específica
                    for full_key in _course_file_map:
                        if full_key.startswith(base_course + " "):
                            sede_part = _normalize(full_key[len(base_course):].strip())
                            sede_words = sede_part.split()
                            if all(w in msg for w in sede_words):
                                return full_key  # sede explícita en el mensaje
                    return base_course  # sin sede → genérico
            return course_key

    # 2. Fallback: buscar por palabras de las claves del catálogo
    for key in sorted(_course_file_map.keys(), key=len, reverse=True):
        words = key.split()
        if all(w in msg for w in words):
            for base_course in _multi_sede:
                if key.startswith(base_course + " "):
                    sede_part = _normalize(key[len(base_course):].strip())
                    sede_words = sede_part.split()
                    if all(w in msg for w in sede_words):
                        return key
                    else:
                        return base_course
            return key

    return None


def get_pdf_url_for_course(course_key: str):
    """
    Retorna (url_descarga, nombre_archivo, file_id) para el curso dado, o None.
    """
    if not _files_metadata or not _course_file_map:
        return None

    filename = _course_file_map.get(course_key)
    if not filename:
        return None

    file_id = _files_metadata.get(filename)
    if not file_id:
        fn_norm = _normalize(filename)
        for fn, fid in _files_metadata.items():
            if _normalize(fn) == fn_norm:
                file_id = fid
                break

    if not file_id:
        return None

    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    return url, filename, file_id


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
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            text = re.sub(r"  +", " ", text)
            text = re.sub(r" \n", "\n", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            pages.append(text.strip())
        return "\n\n".join(pages).strip()
    except Exception as e:
        print(f"❌ Error extrayendo texto del PDF: {e}")
        return ""


def make_files_public():
    """Hace que todos los PDFs de la carpeta sean accesibles públicamente."""
    try:
        service = _get_drive_service()
        for filename, file_id in _files_metadata.items():
            try:
                service.permissions().create(
                    fileId=file_id,
                    body={"type": "anyone", "role": "reader"},
                ).execute()
            except Exception:
                pass
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

        for file in files:
            _files_metadata[file["name"]] = file["id"]

        _build_catalog(list(_files_metadata.keys()))

        all_text = []
        for file in files:
            try:
                request = service.files().get_media(fileId=file["id"])
                pdf_bytes = request.execute()
                _pdf_bytes_cache[file["id"]] = pdf_bytes
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
    """Retorna bytes del PDF — primero desde cache en memoria, si no descarga de Drive."""
    if file_id in _pdf_bytes_cache:
        return _pdf_bytes_cache[file_id]
    try:
        service = _get_drive_service()
        request = service.files().get_media(fileId=file_id)
        data = request.execute()
        _pdf_bytes_cache[file_id] = data
        return data
    except Exception as e:
        print(f"❌ Error descargando PDF {file_id}: {e}")
        return None
