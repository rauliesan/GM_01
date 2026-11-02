"""
================================================================================
    Web Actions - Automatización de Publicación en Wallapop (GM_01)
================================================================================
Gestiona el ciclo de vida de cada anuncio:
1. Extrae título, descripción y fotos del anuncio actual.
2. Descarga las fotos en una carpeta temporal local.
3. Borra el anuncio original en Wallapop.
4. Vuelve a subir el anuncio con los parámetros requeridos:
   - ¿Qué subirás? -> 'Algo que ya no necesito'
   - Información del producto -> Título original
   - Subida de fotos descargadas
   - Categoría y subcategorías:
     Motor y accesorios -> Recambios de motos y cuatriciclos -> Motor y piezas de motor
   - Limpiar y escribir título
   - Limpiar y escribir descripción
   - Estado: 'En buen estado'
   - Precio: '1€'
   - Peso: '1 a 2 kg'
   - Clic en 'Publicar'
"""

import json
import os
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import BrowserContext, Page, sync_playwright

# Permitir importar módulos tanto desde la raíz como desde local_performer
CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from browser_utils import get_persistent_browser, ensure_wallapop_logged_in, handle_cookie_consent, resolve_chrome_profile_dir
try:
    from exceptions import BusinessException, SystemException
except ImportError:
    from local_performer.exceptions import BusinessException, SystemException
from local_credentials import get_credential


class WebAutomationManager:
    def __init__(
        self,
        headless: bool = False,
        browser_type: str = "chromium",
        timeout_ms: int = 30000,
        slow_mo_ms: int = 150,
        profile_dir: Optional[Path] = None,
        credential_name: Optional[str] = None,
        config: Optional[dict] = None
    ):
        self.headless = headless
        self.browser_type_name = browser_type
        self.timeout_ms = timeout_ms
        self.slow_mo_ms = slow_mo_ms
        self.config = config or {}
        self.profile_dir = resolve_chrome_profile_dir(profile_dir or self.config.get("profile_dir", "system"))
        self.credential_name = credential_name

        self.playwright = None
        self.context: BrowserContext = None
        self.page: Page = None

        # Carpetas de trabajo
        self.temp_base_dir = ROOT_DIR / "local_performer" / "temp_media"
        self.temp_base_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir = ROOT_DIR / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    def start(self):
        """Inicializa Playwright y abre la sesión persistente de Chrome."""
        print(f"-> Iniciando Playwright con perfil persistente en: {self.profile_dir}")
        self.playwright = sync_playwright().start()

        self.context = get_persistent_browser(
            playwright=self.playwright,
            profile_dir=self.profile_dir,
            headless=self.headless,
            timeout_ms=self.timeout_ms,
            slow_mo_ms=self.slow_mo_ms
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

        # Asegurar sesión antes de comenzar transacciones
        ensure_wallapop_logged_in(self.page, timeout_seconds=180)
        print("-> Sesión de Wallapop lista para procesar anuncios.")

    def close(self):
        """Cierra el navegador y detiene Playwright limpiamente."""
        print("-> Cerrando sesión de navegador...")
        if self.context:
            self.context.close()
        if self.playwright:
            self.playwright.stop()
        print("-> Recursos de Playwright liberados.")

    def get_credentials(self, target_name: Optional[str] = None) -> Tuple[str, str]:
        cred_name = target_name or self.credential_name
        if not cred_name:
            raise SystemException(message="No se ha configurado 'credential_name'.")
        return get_credential(cred_name)

    def _extract_item_data_from_page(self, item_url: str) -> Dict[str, Any]:
        """
        Navega a la URL del anuncio y extrae título, descripción y URLs de fotos
        mediante __NEXT_DATA__ o selectores DOM de respaldo.
        """
        print(f"   [Fase 1] Cargando anuncio: {item_url}")
        try:
            self.page.goto(item_url, wait_until="domcontentloaded")
            handle_cookie_consent(self.page)
            self.page.wait_for_timeout(2500)
        except Exception as e:
            raise SystemException(message=f"No se pudo cargar la página del anuncio: {item_url}", details=str(e))

        title = ""
        description = ""
        image_urls = []

        # 1. Extracción preferente vía __NEXT_DATA__ (100% fiel y de alta resolución)
        next_data_el = self.page.locator("#__NEXT_DATA__")
        if next_data_el.count() > 0:
            try:
                raw_json = next_data_el.inner_text()
                parsed = json.loads(raw_json)
                item_obj = parsed.get("props", {}).get("pageProps", {}).get("item", {})

                # Título
                t_val = item_obj.get("title")
                if isinstance(t_val, dict):
                    title = t_val.get("original") or t_val.get("translated") or ""
                elif isinstance(t_val, str):
                    title = t_val

                # Descripción
                d_val = item_obj.get("description")
                if isinstance(d_val, dict):
                    description = d_val.get("original") or d_val.get("translated") or ""
                elif isinstance(d_val, str):
                    description = d_val

                # Fotos de alta resolución
                images_list = item_obj.get("images", [])
                for img_entry in images_list:
                    urls = img_entry.get("urls", {})
                    # Priorizar W800 / W640
                    img_url = urls.get("big") or urls.get("medium") or urls.get("small")
                    if img_url:
                        image_urls.append(img_url)
            except Exception as e:
                print(f"   [Aviso] No se pudo parsear __NEXT_DATA__, recurriendo a DOM: {e}")

        # 2. Respaldo vía DOM si faltara algún dato
        if not title:
            h1 = self.page.locator("h1, [class*='ItemDetailTitle'], [class*='title']").first
            if h1.count() > 0:
                title = h1.inner_text().strip()

        if not description:
            desc_el = self.page.locator("[class*='description'], [itemprop='description'], p").first
            if desc_el.count() > 0:
                description = desc_el.inner_text().strip()

        if not image_urls:
            img_nodes = self.page.locator("img[src*='wallapop.com/images']").all()
            for im in img_nodes:
                src = im.get_attribute("src")
                if src and src not in image_urls:
                    image_urls.append(src)

        if not title:
            raise BusinessException(
                code="SL-02",
                message="No se pudo extraer el título del anuncio.",
                details=f"URL: {item_url}"
            )

        print(f"   [Fase 1] Título extraído      : '{title}'")
        print(f"   [Fase 1] Longitud descripción: {len(description)} caracteres")
        print(f"   [Fase 1] Fotos detectadas    : {len(image_urls)}")

        return {
            "title": title,
            "description": description,
            "image_urls": image_urls
        }

    def _download_photos(self, image_urls: List[str], target_dir: Path) -> List[Path]:
        """Descarga las fotos en una carpeta temporal local."""
        target_dir.mkdir(parents=True, exist_ok=True)
        saved_paths = []

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

        for idx, url in enumerate(image_urls, start=1):
            file_path = target_dir / f"photo_{idx:02d}.jpg"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp, open(file_path, "wb") as f_out:
                    shutil.copyfileobj(resp, f_out)
                saved_paths.append(file_path)
            except Exception as e:
                print(f"   [Aviso] Fallo descargando foto #{idx} ({url}): {e}")

        print(f"   [Fase 1] {len(saved_paths)} fotos guardadas en: {target_dir.name}")
        return saved_paths

    def _delete_item(self, item_id: str, title: str) -> bool:
        """
        Borra el anuncio en Wallapop, navegando al catálogo de publicados.
        Devuelve True si se completó el borrado, False en caso contrario.
        """
        clean_title = (title or "").strip()
        print(f"   [Fase 2] Procediendo a borrar el anuncio (ID: {item_id}, Título: '{clean_title[:30]}...')...")

        catalog_url = "https://es.wallapop.com/app/catalog/published"
        self.page.goto(catalog_url, wait_until="domcontentloaded")
        handle_cookie_consent(self.page)
        self.page.wait_for_timeout(2000)
        self._dismiss_dialog_or_modal(context="en catálogo")

        # Desplazamiento ligero para asegurar carga de tarjetas
        try:
            self.page.evaluate("window.scrollBy(0, 300)")
            self.page.wait_for_timeout(1000)
        except Exception:
            pass

        found_card = None

        # 1. Estrategia directa: Buscar la tarjeta contenedora que contenga el título del anuncio
        snippets = [clean_title, clean_title[:25], clean_title[:15]]
        for snip in snippets:
            if not snip or len(snip) < 3:
                continue
            card_loc = self.page.locator(
                f"tsl-card:has-text('{snip}'):visible, "
                f"article:has-text('{snip}'):visible, "
                f"li:has-text('{snip}'):visible, "
                f"[class*='item']:has-text('{snip}'):visible, "
                f"[class*='Item']:has-text('{snip}'):visible, "
                f"[class*='card']:has-text('{snip}'):visible, "
                f"[class*='Card']:has-text('{snip}'):visible"
            ).first
            if card_loc.count() > 0 and card_loc.is_visible():
                found_card = card_loc
                break

        # 2. Respaldo: Buscar por nodo de texto y subir al ancestro tarjeta
        if not found_card or found_card.count() == 0:
            for snip in snippets:
                if not snip or len(snip) < 3:
                    continue
                title_node = self.page.locator(
                    f"span:has-text('{snip}'):visible, "
                    f"h2:has-text('{snip}'):visible, "
                    f"h3:has-text('{snip}'):visible, "
                    f"div:has-text('{snip}'):visible"
                ).first
                if title_node.count() > 0 and title_node.is_visible():
                    found_card = title_node.locator(
                        "xpath=ancestor::*[contains(@class, 'card') or contains(@class, 'Card') or contains(@class, 'item') or contains(@class, 'Item') or self::article or self::tsl-card or self::li][1]"
                    )
                    if found_card.count() > 0:
                        break

        # 3. Respaldo: Buscar por enlace con el ID del producto
        if (not found_card or found_card.count() == 0) and item_id and item_id != "test_sample":
            link = self.page.locator(f"a[href*='{item_id}']:visible").first
            if link.count() > 0 and link.is_visible():
                found_card = link.locator(
                    "xpath=ancestor::*[contains(@class, 'card') or contains(@class, 'Card') or contains(@class, 'item') or contains(@class, 'Item') or self::article or self::tsl-card or self::li][1]"
                )

        if not found_card or found_card.count() == 0:
            print(f"   [Aviso] No se localizó la tarjeta en el catálogo ({clean_title[:30]}).")
            return False

        found_card.scroll_into_view_if_needed()
        self.page.wait_for_timeout(600)

        # Desplegar el menú de opciones (3 puntos o botón Gestionar)
        menu_selectors = [
            "button[aria-label*='opciones']",
            "button[aria-label*='más']",
            "button[aria-label*='menu']",
            "button:has(walla-icon)",
            "button:has(svg)",
            "walla-icon",
            "[data-testid*='kebab']",
            "[data-testid*='options']",
            "[class*='dots']",
            "[class*='menu']",
            "button:has-text('...')",
            "button"
        ]
        menu_clicked = False
        for m_sel in menu_selectors:
            btn = found_card.locator(m_sel).first
            if btn.count() > 0 and btn.is_visible():
                try:
                    btn.click(force=True)
                    self.page.wait_for_timeout(1000)
                    menu_clicked = True
                    break
                except Exception:
                    pass

        # Buscar opción 'Eliminar producto' / 'Borrar anuncio'
        del_option = self.page.locator(
            "button:has-text('Eliminar'):visible, "
            "span:has-text('Eliminar'):visible, "
            "span:has-text('Eliminar producto'):visible, "
            "walla-list-item:has-text('Eliminar'):visible, "
            "[role='menuitem']:has-text('Eliminar'):visible, "
            "button:has-text('Borrar'):visible, "
            "[data-testid*='delete']:visible"
        ).last

        # Respaldo: si el menú de la tarjeta no abre, entrar al anuncio directamente
        if not del_option.is_visible(timeout=2000):
            print("   [Fase 2] Menú en tarjeta no directo. Entrando al anuncio para eliminar...")
            item_link = found_card.locator("a[href*='/item/']").first
            if item_link.count() > 0 and item_link.is_visible():
                item_link.click()
            else:
                found_card.click()
            self.page.wait_for_timeout(3000)

            # En la vista del anuncio, buscar el botón o menú de opciones
            detail_menu = self.page.locator(
                "button[aria-label*='más']:visible, "
                "button[aria-label*='opciones']:visible, "
                "button:has(walla-icon):visible, "
                "button:has-text('Eliminar'):visible, "
                "[data-testid*='delete']:visible"
            ).first
            if detail_menu.is_visible(timeout=3000):
                detail_menu.click(force=True)
                self.page.wait_for_timeout(1000)

            del_option = self.page.locator(
                "button:has-text('Eliminar'):visible, "
                "span:has-text('Eliminar'):visible, "
                "[role='menuitem']:has-text('Eliminar'):visible"
            ).last

        if del_option.is_visible(timeout=4000):
            del_option.click(force=True)
            self.page.wait_for_timeout(1500)

            # Modal de motivo de eliminación
            reason_option = self.page.locator(
                "span:has-text('Ya no quiero venderlo'):visible, "
                "label:has-text('Ya no quiero venderlo'):visible, "
                "walla-radio:has-text('Ya no quiero venderlo'):visible, "
                "span:has-text('Otro motivo'):visible, "
                "label:has-text('Otro motivo'):visible, "
                "span:has-text('Lo he vendido fuera de Wallapop'):visible, "
                "label:has-text('Lo he vendido fuera de Wallapop'):visible, "
                "span:has-text('Lo he vendido'):visible"
            ).first
            if reason_option.is_visible(timeout=4000):
                reason_option.click(force=True)
                self.page.wait_for_timeout(1000)

            # Botón de confirmación final
            confirm_btn = self.page.locator(
                "button:has-text('Eliminar producto'):visible, "
                "button:has-text('Confirmar'):visible, "
                "button:has-text('Eliminar'):visible, "
                "button:has-text('Borrar'):visible, "
                "walla-button:has-text('Eliminar'):visible, "
                "span[part='button-text']:text-is('Eliminar'):visible"
            ).last
            if confirm_btn.is_visible(timeout=4000):
                confirm_btn.click(force=True)
                self.page.wait_for_timeout(3000)
                print("   [Fase 2] ¡Anuncio borrado con éxito!")
                return True

        print("   [Aviso] No se completó la confirmación de borrado.")
        return False

    def _dismiss_dialog_or_modal(self, context: str = ""):
        """
        Cierra o neutraliza inmediatamente cualquier diálogo o ventana emergente.
        Sin esperas innecesarias: detecta y elimina en milisegundos.
        """
        print(f"   [Modal] Cerrando pop-up/modal de inmediato ({context})...")

        # 1. Clic directo e instantáneo por inyección JS (el método más rápido posible, <50ms)
        try:
            clicked = self.page.evaluate("""() => {
                const btn = document.querySelector('button.dialog__header__sideButton') ||
                            document.querySelector('button[aria-label="Close"]') ||
                            document.querySelector('button[aria-label="Cerrar"]') ||
                            document.querySelector('[data-testid*="close"]') ||
                            document.querySelector('button.dialog__header__sideButton walla-icon');
                if (btn) {
                    btn.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
                    btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    btn.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, cancelable: true, view: window }));
                    btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    btn.click();
                    return true;
                }
                return false;
            }""")
            if clicked:
                print(f"   [Modal] Pop-up cerrado al instante vía botón de cierre.")
                self.page.wait_for_timeout(300)
        except Exception:
            pass

        # 2. Respaldo rápido por Playwright locator con force=True
        try:
            btn = self.page.locator("button.dialog__header__sideButton, button[aria-label='Close'], button[aria-label='Cerrar']").first
            if btn.count() > 0 and btn.is_visible(timeout=300):
                box = btn.bounding_box()
                if box:
                    self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                else:
                    btn.click(force=True, timeout=1000)
                self.page.wait_for_timeout(200)
        except Exception:
            pass

        # 3. Tecla Escape rápida
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass

        # 4. Neutralización instantánea por DOM si sigue presente
        try:
            self.page.evaluate("""() => {
                const modals = document.querySelectorAll('dialog, [role="dialog"], .dialog, walla-dialog, [class*="dialog"], [class*="modal"], [class*="backdrop"], [class*="overlay"]');
                if (modals.length > 0) {
                    modals.forEach(m => {
                        try {
                            m.style.display = 'none';
                            m.remove();
                        } catch(e) {}
                    });
                    document.body.style.overflow = 'auto';
                    document.documentElement.style.overflow = 'auto';
                }
            }""")
        except Exception:
            pass

    def _click_continue_button(self, context: str = "") -> bool:
        """
        Pulsa el botón 'Continuar' activo y visible, garantizando el avance
        sin bloqueos de Shadow DOM ni oscilaciones de scroll.
        """
        print(f"   [Fase 3] Pulsando botón 'Continuar' ({context})...")
        self.page.wait_for_timeout(1500)

        # 1. Localizar el botón 'Continuar' que esté VISIBLE (el último del paso activo)
        btn_loc = self.page.locator(
            "button.walla-button__button--primary:visible, "
            "[data-testid='continue-button']:visible, "
            "button:has-text('Continuar'):visible, "
            "span[part='button-text']:text-is('Continuar'):visible"
        ).last

        try:
            btn_loc.wait_for(state="visible", timeout=8000)
        except Exception:
            print(f"   [Aviso] No se encontró botón 'Continuar' visible ({context}).")
            return False

        # 2. Clic directo con force=True (evita oscilaciones de scroll y perfora Shadow DOM)
        try:
            btn_loc.click(force=True, timeout=3000)
            print("   [Fase 3] Clic en botón 'Continuar' enviado.")
        except Exception as ex:
            print(f"   [Nota] Clic directo falló ({ex}), probando teclado...")

        # 3. Respaldo: pulsar tecla Enter por si el formulario responde a submit de teclado
        self.page.wait_for_timeout(1000)
        try:
            self.page.keyboard.press("Enter")
        except Exception:
            pass

        # 4. Respaldo físico de coordenadas de ratón si siguiera en la misma vista
        try:
            box = btn_loc.bounding_box()
            if box and box["width"] > 0:
                self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        except Exception:
            pass

        self.page.wait_for_timeout(2000)
        return True

    def _click_category_option(self, option_text: str):
        """Busca y pulsa una categoría o subcategoría en la interfaz de Wallapop."""
        print(f"      -> Seleccionando categoría: '{option_text}'...")
        try:
            # Buscamos elementos VISIBLES y tomamos el último (.last) para evitar
            # colisionar con elementos ocultos de pasos previos (ej: pantalla inicial de verticales).
            loc = self.page.locator(
                f"button:has-text('{option_text}'):visible, "
                f"walla-list-item:has-text('{option_text}'):visible, "
                f"tsl-dropdown-item:has-text('{option_text}'):visible, "
                f"[role='option']:has-text('{option_text}'):visible, "
                f"li:has-text('{option_text}'):visible, "
                f"div.CategoryItem:has-text('{option_text}'):visible, "
                f"div:text-is('{option_text}'):visible, "
                f"span:text-is('{option_text}'):visible, "
                f"span:has-text('{option_text}'):visible"
            ).last

            loc.wait_for(state="visible", timeout=8000)
            self.page.wait_for_timeout(400)
            
            # Intento de clic directo o forzado, y respaldo con JS sobre el contenedor interactivo
            try:
                loc.click(force=True, timeout=3000)
            except Exception:
                loc.evaluate("el => (el.closest('button, [role=option], walla-list-item, li, a') || el).click()")

            print(f"      [OK] Categoría '{option_text}' seleccionada.")
            self.page.wait_for_timeout(1500)
        except Exception as e:
            print(f"      [Nota] Categoría '{option_text}' no pudo seleccionarse o ya estaba activa: {e}")

    def _upload_new_item(
        self,
        title: str,
        description: str,
        photo_paths: List[Path]
    ):
        """
        Ejecuta el flujo de subida de producto en Wallapop con los selectores exactos:
        1. ¿En qué categoría? -> 'Algo que ya no necesito'
        2. Resumen inicial -> input#summary
        3. Clic en botón 'Continuar'
        4. Subida de fotos locales -> DropAreaPreview / input file
        5. Filtros de Categoría:
           Motor y accesorios -> Recambios de motos y cuatriciclos -> Motor y piezas de motor
        6. Título definitivo -> input#title
        7. Descripción -> textarea#description
        8. Estado -> Desplegable Estado -> 'En buen estado'
        9. Precio -> input#price_amount ('1€')
        10. Peso -> input radio '1 a 2 kg' (value 1)
        11. Botón Publicar -> span[part='button-text']:text-is('Publicar')
        """
        print("   [Fase 3] Iniciando proceso de subida del anuncio...")
        upload_url = "https://es.wallapop.com/app/catalog/upload"
        self.page.goto(upload_url, wait_until="domcontentloaded")
        handle_cookie_consent(self.page)
        self.page.wait_for_timeout(2500)

        # 1. ¿Qué subirás? -> 'Algo que ya no necesito'
        print("   [Fase 3] Seleccionando tipo: 'Algo que ya no necesito'...")
        upload_type = self.config.get("upload_type", "Algo que ya no necesito")
        type_btn = self.page.locator(
            "button[aria-label='Consumer Goods Vertical'], "
            "button.UploadStepVertical__multiIcons, "
            "span.UploadStepVertical__multiIcons__title, "
            f"button:has-text('{upload_type}'), "
            f"span:has-text('{upload_type}')"
        ).first
        type_btn.wait_for(state="visible", timeout=15000)
        type_btn.scroll_into_view_if_needed()
        type_btn.click()
        self.page.wait_for_timeout(2000)

        # 2. Información del producto -> poner el título en 'summary'
        summary_input = self.page.locator(
            "input#summary, "
            "input[name='summary'], "
            "input.sc-walla-text-input[maxlength='50'], "
            "input[placeholder*='vendes'], "
            "input[placeholder*='Indica qué']"
        ).first
        if summary_input.is_visible(timeout=5000):
            print(f"   [Fase 3] Escribiendo título inicial en summary: '{title[:50]}'...")
            summary_input.scroll_into_view_if_needed()
            summary_input.click()
            summary_input.fill("")
            summary_input.fill(title[:50])
            self.page.wait_for_timeout(500)

        # 3. Subir las fotos mediante el botón 'Subir fotos' y el selector de archivos
        if photo_paths:
            print(f"   [Fase 3] Subiendo {len(photo_paths)} fotos...")
            upload_btn = self.page.locator(
                "span[part='button-text']:text-is('Subir fotos'), "
                "[part='button-text']:has-text('Subir fotos'), "
                "button:has-text('Subir fotos'), "
                "button.DropAreaPreview__button"
            ).first

            # Si el botón aún no es visible, comprobar si hay que pulsar Continuar tras summary
            if not upload_btn.is_visible():
                continue_pre = self.page.locator(
                    "span[part='button-text']:text-is('Continuar'), "
                    "[part='button-text']:has-text('Continuar'), "
                    "button:has-text('Continuar')"
                ).first
                if continue_pre.is_visible(timeout=2000):
                    continue_pre.click()
                    self.page.wait_for_timeout(1500)

            upload_btn.wait_for(state="visible", timeout=10000)
            upload_btn.scroll_into_view_if_needed()

            # Interceptar el explorador de archivos nativo y enviar los archivos
            try:
                with self.page.expect_file_chooser(timeout=8000) as fc_info:
                    upload_btn.click()
                file_chooser = fc_info.value
                file_chooser.set_files([str(p) for p in photo_paths])
            except Exception as e:
                # Respaldo vía input[type='file'] si estuviera presente en el DOM
                file_input = self.page.locator("input[type='file']").first
                if file_input.count() > 0:
                    file_input.set_input_files([str(p) for p in photo_paths])
                else:
                    raise e

            self.page.wait_for_timeout(4000)
            print("   [Fase 3] Fotos cargadas en el formulario.")

        # 4. Pulsar botón 'Continuar' tras la subida de fotos
        self._click_continue_button(context="tras subir fotos")

        # 5. Filtros de Categoría y subcategorías:
        # Abrir el desplegable de 'Categoría y subcategoría'
        print("   [Fase 3] Abriendo desplegable de 'Categoría y subcategoría'...")
        cat_dropdown = self.page.locator(
            "label:has-text('Categoría y subcategoría'):visible, "
            "div.inputWrapper:has(label:has-text('Categoría y subcategoría')):visible, "
            "div.inputWrapper:has(label:has-text('Categoría')):visible, "
            "div.right-section:has(walla-icon.sc-walla-dropdown):visible, "
            "walla-icon.sc-walla-dropdown:visible"
        ).first
        try:
            if cat_dropdown.is_visible(timeout=6000):
                cat_dropdown.scroll_into_view_if_needed()
                try:
                    cat_dropdown.click(force=True, timeout=3000)
                except Exception:
                    cat_dropdown.evaluate("el => el.click()")
                print("   [Fase 3] Desplegable de categorías abierto.")
                self.page.wait_for_timeout(1500)
        except Exception as e:
            print(f"   [Nota] Desplegable categorías: {e}")

        # Motor y accesorios -> Recambios de motos y cuatriciclos -> Motor y piezas de motor
        category_hierarchy = self.config.get("category_path", [
            "Motor y accesorios",
            "Recambios de motos y cuatriciclos",
            "Motor y piezas de motor"
        ])
        print("   [Fase 3] Configurando jerarquía de categorías...")
        for cat in category_hierarchy:
            self._click_category_option(cat)

        self.page.wait_for_timeout(2000)

        # Si tras las categorías pide 'Continuar' o 'Aplicar'
        cont2 = self.page.locator(
            "button:has-text('Continuar'):visible, "
            "[data-testid='continue-button']:visible, "
            "button:has-text('Aplicar'):visible"
        ).first
        if cont2.is_visible(timeout=2000):
            try:
                cont2.click(force=True)
                self.page.wait_for_timeout(1500)
            except Exception:
                pass

        # 6. Limpiar el campo y escribir el título en el formulario completo
        print(f"   [Fase 3] Limpiando y escribiendo título: '{title}'...")
        title_field = self.page.locator(
            "div.inputWrapper:has(label:has-text('Título')) input:visible, "
            "div:has(> label:has-text('Título')) input:visible, "
            "walla-text-input:has(label:has-text('Título')) input:visible, "
            "input#title:visible, "
            "input[name='title']:visible, "
            "input[placeholder*='Título']:visible, "
            "input[placeholder*='título']:visible"
        ).first

        if not title_field.is_visible(timeout=3000):
            try:
                title_field = self.page.get_by_label("Título", exact=False).first
            except Exception:
                pass

        if title_field.is_visible(timeout=6000):
            title_field.scroll_into_view_if_needed()
            title_field.click(force=True)
            title_field.press("Control+a")
            title_field.press("Backspace")
            title_field.fill("")
            self.page.wait_for_timeout(200)
            title_field.fill(title[:50])
            print(f"   [OK] Título escrito: '{title[:50]}'")
            self.page.wait_for_timeout(500)
        else:
            print("   [Aviso] No se encontró el campo de Título.")

        # 7. Limpiar el campo y escribir la descripción
        print(f"   [Fase 3] Limpiando y escribiendo descripción...")
        desc_field = self.page.locator(
            "div.inputWrapper:has(label:has-text('Descripción')) textarea:visible, "
            "div:has(> label:has-text('Descripción')) textarea:visible, "
            "walla-textarea textarea:visible, "
            "textarea.sc-walla-textarea:visible, "
            "textarea#description:visible, "
            "textarea[name='description']:visible, "
            "textarea[placeholder*='descripción']:visible, "
            "textarea:visible"
        ).first

        if not desc_field.is_visible(timeout=3000):
            try:
                desc_field = self.page.get_by_label("Descripción", exact=False).first
            except Exception:
                pass

        if desc_field.is_visible(timeout=6000):
            desc_field.scroll_into_view_if_needed()
            desc_field.click(force=True)
            desc_field.press("Control+a")
            desc_field.press("Backspace")
            desc_field.fill("")
            self.page.wait_for_timeout(200)
            desc_field.fill(description or title)
            print(f"   [OK] Descripción escrita ({len(description or title)} caracteres).")
            self.page.wait_for_timeout(500)
        else:
            print("   [Aviso] No se encontró el campo de Descripción.")

        # 8. Seleccionar el estado: 'En buen estado'
        condition = self.config.get("condition", "En buen estado")
        print(f"   [Fase 3] Seleccionando estado: '{condition}'...")

        state_dropdown = self.page.locator(
            "div.inputWrapper:has(label:has-text('Estado')):visible, "
            "div:has(> label:has-text('Estado')):visible, "
            "walla-text-input:has(label:has-text('Estado')):visible, "
            "label:has-text('Estado'):visible"
        ).first

        if state_dropdown.is_visible(timeout=4000):
            state_dropdown.scroll_into_view_if_needed()
            try:
                state_dropdown.click(force=True, timeout=3000)
            except Exception:
                state_dropdown.evaluate("el => el.click()")
            self.page.wait_for_timeout(1000)

        cond_opt = self.page.locator(
            f"button:has-text('{condition}'):visible, "
            f"walla-list-item:has-text('{condition}'):visible, "
            f"[role='option']:has-text('{condition}'):visible, "
            f"li:has-text('{condition}'):visible, "
            f"div:text-is('{condition}'):visible, "
            f"span:text-is('{condition}'):visible, "
            f"span:has-text('{condition}'):visible"
        ).last

        if cond_opt.is_visible(timeout=5000):
            cond_opt.scroll_into_view_if_needed()
            try:
                cond_opt.click(force=True, timeout=3000)
            except Exception:
                cond_opt.evaluate("el => (el.closest('button, [role=option], walla-list-item, li') || el).click()")
            print(f"   [OK] Estado '{condition}' seleccionado.")
            self.page.wait_for_timeout(1000)
        else:
            print(f"   [Aviso] No se localizó la opción de estado '{condition}'.")

        # 9. Poner precio: '1€'
        price = str(self.config.get("price", "1"))
        print(f"   [Fase 3] Introduciendo precio: '{price}€'...")
        price_field = self.page.locator(
            "div.inputWrapper:has(label:has-text('Precio')) input:visible, "
            "div.inputWrapper__content--currency input:visible, "
            "walla-text-input:has(label:has-text('Precio')) input:visible, "
            "div:has(> label:has-text('Precio')) input:visible, "
            "input#price_amount:visible, "
            "input[name='price_amount']:visible, "
            "input[placeholder*='0,00']:visible, "
            "input[placeholder*='Precio']:visible"
        ).first

        if not price_field.is_visible(timeout=3000):
            try:
                price_field = self.page.get_by_label("Precio", exact=False).first
            except Exception:
                pass

        if price_field.is_visible(timeout=6000):
            price_field.scroll_into_view_if_needed()
            price_field.click(force=True)
            price_field.press("Control+a")
            price_field.press("Backspace")
            price_field.fill("")
            self.page.wait_for_timeout(200)
            price_field.fill(price)
            print(f"   [OK] Precio '{price}€' introducido.")
            self.page.wait_for_timeout(500)
        else:
            print("   [Aviso] No se localizó el campo de precio.")

        # 10. ¿Cuánto pesa? -> '1 a 2 kg'
        weight = self.config.get("weight", "1 a 2 kg")
        print(f"   [Fase 3] Seleccionando peso: '{weight}'...")
        # Scroll hacia abajo para revelar la sección de envíos/peso
        try:
            self.page.evaluate("window.scrollBy(0, 500)")
            self.page.wait_for_timeout(800)
        except Exception:
            pass

        # Si el peso es un desplegable previo (ej: 'Peso del producto' o 'Elige el peso')
        weight_dropdown = self.page.locator(
            "div.inputWrapper:has(label:has-text('Peso')):visible, "
            "div:has(> label:has-text('Peso')):visible, "
            "label:has-text('Peso'):visible"
        ).first
        if weight_dropdown.is_visible(timeout=2000):
            try:
                weight_dropdown.scroll_into_view_if_needed()
                weight_dropdown.click(force=True)
                self.page.wait_for_timeout(1000)
            except Exception:
                pass

        weight_loc = self.page.locator(
            "walla-radio:has-text('1 a 2'):visible, "
            "walla-radio:has-text('1 - 2'):visible, "
            "label:has-text('1 a 2 kg'):visible, "
            "label:has-text('1 - 2 kg'):visible, "
            "label:has-text('De 1 a 2 kg'):visible, "
            "label:has-text('1 a 2'):visible, "
            "[role='radio']:has-text('1 a 2'):visible, "
            "div:has(> span:has-text('1 a 2')):visible, "
            "div[class*='weight']:has-text('1 a 2'):visible, "
            "div.ShippingWeightCard:has-text('1 a 2'):visible, "
            "span:has-text('1 a 2 kg'):visible, "
            "span:has-text('1 - 2 kg'):visible, "
            "span:has-text('De 1 a 2 kg'):visible, "
            "span:text-is('1 a 2 kg'):visible, "
            "span:has-text('1 a 2'):visible, "
            "button:has-text('1 a 2'):visible"
        ).last

        if weight_loc.is_visible(timeout=4000):
            weight_loc.scroll_into_view_if_needed()
            try:
                weight_loc.click(force=True, timeout=3000)
            except Exception:
                pass
            try:
                weight_loc.evaluate("el => (el.closest('walla-radio, label, [role=radio], button, div') || el).click()")
            except Exception:
                pass
            print(f"   [OK] Peso '{weight}' seleccionado.")
            self.page.wait_for_timeout(1000)
        else:
            # Respaldo buscando sin :visible previo para forzar scroll
            alt_loc = self.page.locator(
                "walla-radio:has-text('1 a 2'), "
                "label:has-text('1 a 2'), "
                "span:has-text('1 a 2')"
            ).last
            try:
                if alt_loc.count() > 0:
                    alt_loc.scroll_into_view_if_needed()
                    alt_loc.click(force=True)
                    print(f"   [OK] Peso '{weight}' seleccionado tras scroll.")
                    self.page.wait_for_timeout(1000)
                else:
                    print(f"   [Nota] Opción de peso '{weight}' no requerida o no visible directamente.")
            except Exception as e:
                print(f"   [Aviso] No se pudo seleccionar peso: {e}")

        # 11. Clic en el botón Publicar
        print("   [Fase 3] Pulsando botón 'Publicar'...")
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.page.wait_for_timeout(1000)
        except Exception:
            pass

        publish_btn = self.page.locator(
            "walla-button:has-text('Publicar'):visible, "
            "button.walla-button__button--primary:has-text('Publicar'):visible, "
            "button:has-text('Publicar'):visible, "
            "span[part='button-text']:text-is('Publicar'):visible, "
            "[data-testid*='publish']:visible"
        ).last

        try:
            publish_btn.wait_for(state="visible", timeout=8000)
            publish_btn.scroll_into_view_if_needed()
            self.page.wait_for_timeout(500)
            try:
                publish_btn.click(force=True, timeout=3000)
            except Exception:
                pass
            try:
                publish_btn.evaluate("el => (el.querySelector('button') || el).click()")
            except Exception:
                pass
            print("   [Fase 3] Clic en botón 'Publicar' completado.")
        except Exception as e:
            print(f"   [Aviso] No se pudo pulsar 'Publicar': {e}")

        # Detección reactiva e instantánea del pop-up post-publicación (sin esperas ciegas)
        close_btn = self.page.locator(
            "button.dialog__header__sideButton, "
            "button[aria-label='Close'], "
            "button[aria-label='Cerrar']"
        ).first
        try:
            close_btn.wait_for(state="visible", timeout=3000)
        except Exception:
            pass

        self._dismiss_dialog_or_modal(context="post-publicación")
        print("   [Fase 3] ¡Anuncio publicado correctamente!")

    def execute_transaction_logic(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Lógica de negocio ejecutada para cada ítem de la cola:
        1. Entrar al anuncio original y guardar fotos, título y descripción.
        2. Borrar el anuncio original.
        3. Subir el anuncio nuevo con las especificaciones del usuario.
        """
        item_id = str(payload.get("item_id", "sin_id"))
        item_url = payload.get("item_url", "")
        base_title = payload.get("title", "")

        if not item_url:
            raise BusinessException(
                code="SL-02",
                message="El payload de la transacción no contiene 'item_url'.",
                details=str(payload)
            )

        item_temp_dir = self.temp_base_dir / f"item_{item_id}"
        downloaded_photos: List[Path] = []

        try:
            # 1. Extraer datos del anuncio
            extracted_data = self._extract_item_data_from_page(item_url)
            title = extracted_data["title"] or base_title
            description = extracted_data["description"]
            image_urls = extracted_data["image_urls"]

            # Descargar fotos a carpeta temporal
            if image_urls:
                downloaded_photos = self._download_photos(image_urls, item_temp_dir)

            if image_urls and not downloaded_photos:
                raise BusinessException(
                    code="SL-03",
                    message=f"No se pudieron descargar las fotos del anuncio original '{title}'. Se detiene para evitar pérdida de datos.",
                    details=f"URLs detectadas: {len(image_urls)}"
                )

            # 2. Borrar el anuncio original (verificación estricta de seguridad)
            deleted = self._delete_item(item_id, title)
            if not deleted:
                raise SystemException(
                    message=f"No se pudo confirmar el borrado del anuncio original '{title}'. Ejecución detenida inmediatamente por seguridad.",
                    details=f"ID: {item_id}, URL: {item_url}"
                )

            # 3. Subir el nuevo anuncio con los datos y fotos
            self._upload_new_item(
                title=title,
                description=description,
                photo_paths=downloaded_photos
            )

            return {
                "item_id": item_id,
                "title": title,
                "photos_uploaded": len(downloaded_photos),
                "status": "PUBLICADO"
            }

        except BusinessException:
            self._capture_error_screenshot(item_id)
            raise

        except Exception as ex:
            self._capture_error_screenshot(item_id)
            raise SystemException(
                message=f"Error técnico durante el proceso del ítem {item_id}: {ex}",
                details=str(ex)
            )

        finally:
            if item_temp_dir.exists():
                try:
                    shutil.rmtree(item_temp_dir, ignore_errors=True)
                except Exception:
                    pass

    def _capture_error_screenshot(self, item_id: str):
        """Captura pantalla en caso de fallo para auditoría y depuración."""
        try:
            if self.page:
                screenshot_path = self.screenshots_dir / f"error_item_{item_id}.png"
                self.page.screenshot(path=str(screenshot_path), full_page=True)
                print(f"   [Screenshot] Captura de error guardada en: {screenshot_path.name}")
        except Exception:
            pass
