"""
================================================================================
    Browser Utils - Gestión de Navegador Playwright y Sesión de Wallapop
================================================================================
Maneja el perfil de Chrome (soporta perfil del sistema portable y perfil local),
evasión de anti-bots, cookies de Wallapop y verificación de sesión iniciada.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, Union
from playwright.sync_api import BrowserContext, Page, Playwright

# Raíz del proyecto
ROOT_DIR = Path(__file__).resolve().parent


def is_chrome_running() -> bool:
    """Comprueba si hay procesos de Google Chrome en ejecución en Windows."""
    try:
        res = subprocess.run(
            ["tasklist", "/fi", "IMAGENAME eq chrome.exe"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return "chrome.exe" in res.stdout.lower()
    except Exception:
        return False


def resolve_chrome_profile_dir(profile_config: Optional[Union[str, Path]] = "system") -> Path:
    """
    Resuelve el directorio de perfil de Chrome de forma 100% portable y dinámica:
    - Si es "system", "auto" o None: detecta automáticamente la carpeta 'User Data'
      del usuario actual del equipo (%LOCALAPPDATA%\\Google\\Chrome\\User Data).
      Funciona en cualquier ordenador sin hardcodear nombres de usuario.
    - Si ya es un Path o una ruta personalizada (ej. '../chrome_profile'): la resuelve relativamente.
    """
    if isinstance(profile_config, Path):
        return profile_config.resolve()

    val = str(profile_config or "system").strip().lower()
    if val in ("system", "system_chrome", "auto", "default"):
        if os.name == "nt":  # Windows
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                sys_profile = Path(local_appdata) / "Google" / "Chrome" / "User Data"
            else:
                sys_profile = Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
        elif sys.platform == "darwin":  # macOS
            sys_profile = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        else:  # Linux
            sys_profile = Path.home() / ".config" / "google-chrome"

        return sys_profile.resolve()

    if "chrome_profile" in val:
        return (ROOT_DIR / "chrome_profile").resolve()

    p = Path(profile_config)
    if not p.is_absolute():
        p = (ROOT_DIR / p).resolve()
    return p


def ensure_chrome_closed_if_system_profile(profile_dir: Path):
    """
    Si se usa el perfil de Chrome del sistema, verifica que no esté abierto
    por el usuario para evitar el bloqueo 'SingletonLock' de Windows.
    """
    p_str = str(profile_dir).lower()
    is_system_profile = "google\\chrome\\user data" in p_str or "google/chrome/user data" in p_str

    if is_system_profile and is_chrome_running():
        print("\n" + "=" * 70)
        print("   [AVISO: CIERRA GOOGLE CHROME EN TU EQUIPO]")
        print("   Has configurado utilizar tu perfil y sesión de Chrome habitual.")
        print("   Google Chrome no permite que dos aplicaciones abran el mismo perfil a la vez.")
        print("")
        print("   -> Por favor, CIERRA las ventanas de Google Chrome.")
        print("   -> El bot continuará automáticamente en cuanto detecte que se ha cerrado.")
        print("=" * 70 + "\n")

        while is_chrome_running():
            time.sleep(2)

        print("-> [OK] Chrome cerrado. Continuando con el proceso...\n")
        time.sleep(1)


def get_persistent_browser(
    playwright: Playwright,
    profile_dir: Path,
    headless: bool = False,
    timeout_ms: int = 30000,
    slow_mo_ms: int = 100
) -> BrowserContext:
    """
    Inicia un contexto de navegador persistente con Chrome para mantener
    la sesión, cookies y almacenamiento local entre ejecuciones.
    """
    profile_dir = Path(profile_dir).resolve()

    # Si es el perfil del sistema, esperar a que el usuario cierre Chrome si estuviera abierto
    ensure_chrome_closed_if_system_profile(profile_dir)

    profile_dir.mkdir(parents=True, exist_ok=True)

    launch_kwargs = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "slow_mo": slow_mo_ms,
        "viewport": {"width": 1366, "height": 768},
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--disable-infobars"
        ],
        "locale": "es-ES"
    }

    try:
        context = playwright.chromium.launch_persistent_context(
            channel="chrome",
            **launch_kwargs
        )
    except Exception as e:
        print(f"-> Intento con canal 'chrome' falló o bloqueado ({e}), usando motor Playwright...")
        context = playwright.chromium.launch_persistent_context(
            **launch_kwargs
        )

    context.set_default_timeout(timeout_ms)

    for page in context.pages:
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    context.on("page", lambda p: p.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"))

    return context


def handle_cookie_consent(page: Page):
    """Detecta y acepta el banner de consentimiento de cookies si aparece."""
    try:
        page.wait_for_timeout(1000)
        selectors = [
            "button:has-text('Aceptar todo')",
            "span:has-text('Aceptar todo')",
            "#cmpwelcomebtnyes",
            "#onetrust-accept-btn-handler",
            "button:has-text('Acepto')",
            "button:has-text('Aceptar')"
        ]
        for sel in selectors:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                print("   [Cookies] Banner de cookies aceptado.")
                page.wait_for_timeout(1000)
                break
    except Exception:
        pass


def is_user_logged_in(page: Page) -> bool:
    """Verifica si el usuario tiene una sesión activa en Wallapop."""
    current_url = page.url.lower()
    if "onboarding" in current_url or "login" in current_url:
        return False

    try:
        has_catalog = "/catalog" in current_url or "/you" in current_url
        has_login_btn = page.locator("button:has-text('Inicia sesión'), a:has-text('Inicia sesión'), button:has-text('Regístrate o inicia sesión')").first.is_visible()
        if has_login_btn:
            return False
        if has_catalog:
            return True
        has_you_menu = page.locator("a[href*='/app/you'], a:has-text('Tú'), button[aria-label*='usuario']").first.is_visible()
        return has_you_menu
    except Exception:
        return False


def ensure_wallapop_logged_in(page: Page, timeout_seconds: int = 180) -> bool:
    """
    Navega a Wallapop y comprueba si hay sesión activa.
    """
    print("-> Verificando sesión en Wallapop...")
    page.goto("https://es.wallapop.com/app/catalog/published", wait_until="domcontentloaded")
    handle_cookie_consent(page)
    page.wait_for_timeout(2000)

    if is_user_logged_in(page):
        print("-> [OK] ¡Sesión detectada correctamente en Wallapop!")
        return True

    print("\n" + "=" * 70)
    print("   [AUTENTICACIÓN WALLAPOP REQUERIDA]")
    print("   No se detectó una sesión activa en este perfil de Chrome.")
    print("   Por favor, INICIA SESIÓN en la ventana de Chrome que se ha abierto.")
    print(f"   Esperando inicio de sesión (tiempo máximo: {timeout_seconds}s)...")
    print("=" * 70 + "\n")

    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        handle_cookie_consent(page)
        if is_user_logged_in(page):
            print("\n-> [ÉXITO] ¡Sesión iniciada detectada! Continuando con el proceso...")
            page.wait_for_timeout(2000)
            return True
        time.sleep(3)

    raise TimeoutError(
        f"Se agotaron los {timeout_seconds} segundos sin que se detectara sesión activa en Wallapop."
    )
