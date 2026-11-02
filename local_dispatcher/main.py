"""
================================================================================
    Dispatcher Local - Extracción de Anuncios 'Despiece' en Wallapop (GM_01)
================================================================================
Abre Chrome mediante Playwright, accede a Wallapop, verifica la sesión del usuario,
escanea los artículos publicados en la cuenta, filtra aquellos cuyo título
contiene 'Despiece' y los encola en la base de datos local SQLite.
"""

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from playwright.sync_api import sync_playwright

# Permitir importar módulos desde la raíz del proyecto
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from browser_utils import get_persistent_browser, ensure_wallapop_logged_in, handle_cookie_consent, resolve_chrome_profile_dir
from local_queue import LocalQueue


def load_config(config_path: Path) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_items_to_process(config: dict) -> List[Dict[str, Any]]:
    """
    Abre Chrome, navega al catálogo de productos publicados en Wallapop,
    identifica los artículos con 'Despiece' en el título y los devuelve estructurados.
    """
    profile_dir = resolve_chrome_profile_dir(config.get("profile_dir", "system"))
    headless = config.get("headless", False)
    timeout_ms = config.get("timeout_ms", 30000)
    slow_mo_ms = config.get("slow_mo_ms", 100)
    keyword = config.get("keyword_filter", "Despiece").strip().lower()

    print(f"-> Conectando a Chrome (headless={headless}, slow_mo={slow_mo_ms}ms)...")
    items_to_queue = []

    with sync_playwright() as playwright:
        context = get_persistent_browser(
            playwright=playwright,
            profile_dir=profile_dir,
            headless=headless,
            timeout_ms=timeout_ms,
            slow_mo_ms=slow_mo_ms
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            # 1. Asegurar sesión iniciada en Wallapop
            ensure_wallapop_logged_in(page, timeout_seconds=180)

            # 2. Navegar a los artículos publicados de la cuenta
            catalog_url = "https://es.wallapop.com/app/catalog/published"
            if "/catalog/published" not in page.url:
                print(f"-> Navegando al catálogo de productos: {catalog_url}")
                page.goto(catalog_url, wait_until="domcontentloaded")
                handle_cookie_consent(page)
                page.wait_for_timeout(3000)

            print("-> Escaneando artículos publicados en tu cuenta...")

            # 3. Hacer scroll para cargar todos los artículos (scroll infinito / lazy load)
            last_height = 0
            for scroll_step in range(10):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1500)
                new_height = page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            # 4. Extraer todos los enlaces y tarjetas de productos
            # Estrategia múltiple: buscar enlaces a /item/ o tarjetas de catálogo
            elements = page.locator("a[href*='/item/'], [class*='ItemCard'], [class*='catalog-item'], tsl-card, article").all()
            print(f"-> Elementos totales detectados en el catálogo: {len(elements)}")

            seen_ids = set()
            for el in elements:
                try:
                    href = el.get_attribute("href") or ""
                    # Buscar enlace interno si el elemento actual es una tarjeta
                    if not href:
                        inner_link = el.locator("a[href*='/item/']").first
                        if inner_link.count() > 0:
                            href = inner_link.get_attribute("href") or ""

                    # Extraer texto del título
                    title_text = ""
                    # Intentar buscar título en encabezados internos o atributos
                    title_loc = el.locator("h2, h3, h4, [class*='title'], [title]").first
                    if title_loc.count() > 0:
                        title_text = title_loc.get_attribute("title") or title_loc.inner_text().strip()
                    if not title_text:
                        title_text = el.inner_text().strip().split("\n")[0]

                    if not title_text or len(title_text) < 3:
                        continue

                    # Extraer ID del producto
                    item_id_match = re.search(r'(\d{8,})', href)
                    if item_id_match:
                        item_id = item_id_match.group(1)
                    else:
                        item_id = re.sub(r'[^a-zA-Z0-9]', '', title_text[:20])

                    if item_id in seen_ids:
                        continue

                    # Comprobar si el título contiene la palabra clave 'despiece'
                    if keyword in title_text.lower():
                        seen_ids.add(item_id)
                        full_url = href if href.startswith("http") else f"https://es.wallapop.com{href}"
                        if not href.startswith("http") and not href.startswith("/"):
                            full_url = f"https://es.wallapop.com/{href}"

                        print(f"   [COINCIDENCIA] ID: {item_id} | Título: '{title_text}'")
                        items_to_queue.append({
                            "reference": f"WALLA-{item_id}",
                            "priority": "High",
                            "max_retries": 3,
                            "payload": {
                                "item_id": item_id,
                                "title": title_text,
                                "item_url": full_url
                            }
                        })
                except Exception as ex:
                    continue

        finally:
            print("-> Cerrando navegador de escaneo para liberar el perfil de Chrome...")
            context.close()

    return items_to_queue


def run_dispatcher():
    config_file = Path(__file__).resolve().parent / "config.json"
    config = load_config(config_file)

    db_path = (Path(__file__).resolve().parent / config["db_path"]).resolve()
    queue_name = config.get("queue_name", "GM01_WallapopQueue")
    clear_previous = config.get("clear_previous_queue", False)

    print("=" * 70)
    print("   DISPATCHER LOCAL (GM_01) - FILTRADO WALLAPOP 'DESPIECE'")
    print("=" * 70)
    print(f"Base de datos : {db_path}")
    print(f"Cola destino  : {queue_name}")
    print(f"Filtro título : '{config.get('keyword_filter', 'Despiece')}'")
    print("-" * 70)

    queue = LocalQueue(db_path=str(db_path))

    # Recuperar posibles ítems que quedaron colgados en InProgress por cortes previos
    recovered = queue.recover_stale_items(queue_name)
    if recovered > 0:
        print(f"-> Se recuperaron {recovered} ítems 'InProgress' colgados y volvieron a 'New'.")

    if clear_previous:
        abandoned = queue.clear_queue(queue_name)
        print(f"-> Limpieza previa: {abandoned} ítems pendientes pasados a 'Abandoned'")

    # Extraer ítems desde Wallapop
    items = extract_items_to_process(config)
    print(f"\n-> Total de anuncios con 'Despiece' encontrados: {len(items)}")

    if not items:
        print("No se encontraron artículos con la palabra 'Despiece' en tu cuenta de Wallapop.")
        return

    # Inserción masiva en SQLite
    item_ids = queue.push_items_bulk(queue_name, items, job_id="DISPATCHER_GM01")
    print(f"-> {len(item_ids)} transacciones insertadas con éxito en SQLite en estado 'New'.")

    # Resumen actual de la cola
    summary = queue.get_summary(queue_name)
    print("-" * 70)
    print("ESTADO ACTUAL DE LA COLA EN SQLITE:")
    for status, count in summary.items():
        if count > 0:
            print(f"   [{status:12}] : {count}")
    print("=" * 70)


if __name__ == "__main__":
    run_dispatcher()
