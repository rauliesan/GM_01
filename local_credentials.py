"""
================================================================================
    Local Credentials - Gestor Seguro y Nativo de Credenciales de Windows
================================================================================
Permite recuperar credenciales almacenadas en el Administrador de Credenciales
de Windows (Credential Manager -> Credenciales genéricas) de forma 100% portable,
sin rutas absolutas y utilizando la API nativa de Windows (sin librerías externas obligatorias).
"""

import sys
import ctypes
from ctypes import wintypes
from typing import Tuple, Optional


# Definición de estructuras de Windows Credential Manager
class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)


def _read_windows_credential_native(target_name: str) -> Optional[Tuple[str, str]]:
    """
    Lee una credencial genérica de Windows usando directamente Advapi32.dll (CredReadW).
    Devuelve (username, password) o None si no existe.
    """
    if sys.platform != "win32":
        return None

    try:
        advapi32 = ctypes.windll.advapi32
        CredReadW = advapi32.CredReadW
        CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_PCREDENTIALW)]
        CredReadW.restype = wintypes.BOOL

        CredFree = advapi32.CredFree
        CredFree.argtypes = [ctypes.c_void_p]

        CRED_TYPE_GENERIC = 1
        pcred = _PCREDENTIALW()

        success = CredReadW(target_name, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred))
        if not success:
            return None

        cred = pcred.contents
        username = cred.UserName or ""

        # Decodificar el blob de la contraseña
        blob_size = cred.CredentialBlobSize
        if blob_size > 0 and cred.CredentialBlob:
            blob_bytes = bytes(ctypes.cast(cred.CredentialBlob, ctypes.POINTER(ctypes.c_ubyte * blob_size)).contents)
            # En Windows Credential Manager se suele guardar en UTF-16-LE o UTF-8
            try:
                password = blob_bytes.decode("utf-16-le")
            except Exception:
                password = blob_bytes.decode("utf-8", errors="ignore")
        else:
            password = ""

        CredFree(pcred)
        return username, password
    except Exception:
        return None


def get_credential(target_name: str) -> Tuple[str, str]:
    """
    Recupera el usuario y la contraseña almacenados bajo una credencial genérica de Windows.
    100% portable y seguro: no requiere rutas ni archivos locales con contraseñas.

    Args:
        target_name: Nombre/Etiqueta con la que se guardó en Windows (ej. 'PortalFacturas').

    Returns:
        Tuple[str, str]: (username, password)

    Raises:
        RuntimeError: Si la credencial no existe en el Administrador de Credenciales de Windows.
    """
    if not target_name:
        raise ValueError("El parámetro 'target_name' no puede estar vacío.")

    # 1. Intentar vía API nativa de Windows (sin dependencias adicionales)
    cred = _read_windows_credential_native(target_name)
    if cred and (cred[0] or cred[1]):
        return cred[0], cred[1]

    # 2. Respaldo opcional vía librería keyring si estuviera instalada
    try:
        import keyring
        k_cred = keyring.get_credential(target_name, None)
        if k_cred and k_cred.username and k_cred.password:
            return k_cred.username, k_cred.password
    except Exception:
        pass

    # 3. Error amigable con instrucciones precisas para el desarrollador
    raise RuntimeError(
        f"\n[ERROR CREDENCIALES] No se encontró la credencial '{target_name}' en este equipo.\n"
        f"Para configurarla en Windows:\n"
        f"  Opción A (Interfaz gráfica):\n"
        f"    1. Pulsa la tecla Windows y busca 'Administrador de credenciales'.\n"
        f"    2. Abre 'Credenciales de Windows' -> 'Credenciales genéricas'.\n"
        f"    3. Pulsa 'Agregar una credencial genérica' con:\n"
        f"       - Dirección de red o Internet: {target_name}\n"
        f"       - Nombre de usuario: <tu_usuario>\n"
        f"       - Contraseña: <tu_contraseña>\n"
        f"  Opción B (Línea de comandos CMD / PowerShell):\n"
        f"    cmdkey /generic:{target_name} /user:<tu_usuario> /pass:<tu_contraseña>\n"
    )
