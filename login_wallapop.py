"""
================================================================================
    Login Wallapop - Asistente de Inicio de Sesión Inicial
================================================================================
Abre el navegador con el perfil persistente 'chrome_profile' para que puedas
iniciar sesión en tu cuenta de Wallapop cómodamente una sola vez.
La sesión quedará guardada para el Dispatcher y el Performer.
"""

import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from browser_utils import get_persistent_browser, ensure_wallapop_logged_in

ROOT_DIR = Path(__file__).resolve().parent


def main():
    config_path = ROOT_DIR / "local_dispatcher" / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    profile_dir = (ROOT_DIR / config.get("profile_dir", "chrome_profile")).resolve()

    print("=" * 70)
    print("   ASISTENTE DE INICIO DE SESIÓN WALLAPOP (GM_01)")
    print("=" * 70)
    print(f"Directorio de perfil: {profile_dir}")
    print("Abriendo Google Chrome...")
    print("-" * 70)

    with sync_playwright() as playwright:
        context = get_persistent_browser(
            playwright=playwright,
            profile_dir=profile_dir,
            headless=False,
            timeout_ms=60000,
            slow_mo_ms=100
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            ensure_wallapop_logged_in(page, timeout_seconds=300)
            print("\n" + "=" * 70)
            print("   ¡SESIÓN GUARDADA CORRECTAMENTE!")
            print("   Ya puedes ejecutar el Dispatcher o el Pipeline completo:")
            print("     python run_pipeline.py --all")
            print("     python run_pipeline.py --dispatcher")
            print("     python run_pipeline.py --performer")
            print("=" * 70 + "\n")
        except Exception as e:
            print(f"\n[ERROR] {e}")
        finally:
            print("Cerrando navegador...")
            context.close()


if __name__ == "__main__":
    main()

