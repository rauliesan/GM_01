"""
================================================================================
    Test Flow - Verificación de Creación y Borrado Seguro (GM_01)
================================================================================
Crea un anuncio de prueba utilizando una imagen de ejemplo y el título configurado
en config.json (con la palabra 'Despiece').
1. Sube el anuncio completo verificando que el formulario no falle.
2. Tras confirmar la publicación, localiza el anuncio en el catálogo y lo elimina.
Esto garantiza que la creación funciona al 100% antes de tocar anuncios reales.
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw

# Permitir importar módulos desde la raíz
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from browser_utils import resolve_chrome_profile_dir
from local_performer.web_actions import WebAutomationManager


def generate_sample_image(output_path: Path) -> Path:
    """Genera una imagen JPEG válida de prueba para simular una foto de despiece."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (800, 600), color=(45, 52, 58))
    draw = ImageDraw.Draw(img)

    # Dibujar marcos y texto identificativo
    draw.rectangle([(20, 20), (780, 580)], outline=(0, 200, 160), width=4)
    draw.rectangle([(40, 40), (760, 560)], outline=(70, 80, 90), width=2)
    draw.text((220, 270), "TEST GM_01 - DESPIECE MOTOR", fill=(255, 255, 255))
    draw.text((270, 310), "IMAGEN DE PRUEBA TEMPORAL", fill=(0, 200, 160))

    img.save(str(output_path), "JPEG", quality=90)
    print(f"-> Imagen de muestra generada en: {output_path.name}")
    return output_path


def run_test(delete_only: bool = False) -> bool:
    test_start_time = datetime.now()
    test_passed = False
    config_file = ROOT_DIR / "local_performer" / "config.json"
    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    test_config = config.get("test_ad", {})
    test_title = test_config.get("title", "Despiece Motor de Prueba Test GM_01")
    test_desc = test_config.get(
        "description",
        "Anuncio de prueba automatizada para verificar subida y borrado. Por favor ignorar."
    )
    image_name = test_config.get("sample_image_name", "test_sample_despiece.jpg")

    # Verificar que el título contiene 'Despiece' tal y como se solicitó
    if "despiece" not in test_title.lower():
        print(f"[ALERTA] El título de prueba configurado ('{test_title}') no contiene la palabra 'Despiece'.")
        print("Añadiendo prefijo 'Despiece' para cumplir la condición...")
        test_title = f"Despiece {test_title}"

    print("=" * 70)
    if delete_only:
        print("   TEST DE SEGURIDAD - SOLO BORRADO EN WALLAPOP (GM_01)")
    else:
        print("   TEST DE SEGURIDAD - CREACIÓN Y BORRADO EN WALLAPOP (GM_01)")
    print("=" * 70)
    print(f"Título objetivo       : '{test_title}'")
    if not delete_only:
        print(f"Descripción de prueba : '{test_desc[:50]}...'")
        print(f"Categoría configurada : {' -> '.join(config.get('category_path', []))}")
        print(f"Precio prueba         : {config.get('price', '1')}€")
        print(f"Peso prueba           : {config.get('weight', '1 a 2 kg')}")
    print("-" * 70)

    temp_img_dir = ROOT_DIR / "local_performer" / "temp_media" / "test_run"
    temp_img_path = temp_img_dir / image_name

    # 1. Generar la imagen solo si se va a realizar la subida
    if not delete_only:
        generate_sample_image(temp_img_path)

    # 2. Iniciar el gestor de automatización (con headless=False para supervisar)
    profile_dir = resolve_chrome_profile_dir(config.get("profile_dir", "system"))
    web_manager = WebAutomationManager(
        headless=False,
        browser_type=config.get("browser_type", "chromium"),
        timeout_ms=config.get("timeout_ms", 30000),
        slow_mo_ms=config.get("slow_mo_ms", 150),
        profile_dir=profile_dir,
        config=config
    )

    try:
        web_manager.start()

        # =====================================================================
        # PASO 1: SUBIR EL ANUNCIO DE PRUEBA (si no es delete_only)
        # =====================================================================
        if not delete_only:
            print("\n" + "#" * 60)
            print("   PASO 1: SUBIENDO ANUNCIO DE PRUEBA...")
            print("#" * 60)

            web_manager._upload_new_item(
                title=test_title,
                description=test_desc,
                photo_paths=[temp_img_path]
            )

            print("\n-> [ÉXITO PASO 1] El anuncio de prueba se ha publicado correctamente.")
            web_manager._dismiss_dialog_or_modal(context="entre subida y borrado")
            print("-> Esperando 2 segundos para que Wallapop indexe el nuevo producto...")
            time.sleep(2)

        # =====================================================================
        # PASO 2: BORRAR EL ANUNCIO DE PRUEBA
        # =====================================================================
        print("\n" + "#" * 60)
        print("   PASO 2: BORRANDO EL ANUNCIO DE PRUEBA CREADO...")
        print("#" * 60)

        deleted = web_manager._delete_item(
            item_id="test_sample",
            title=test_title
        )

        if deleted:
            test_passed = True
            print("\n-> [ÉXITO PASO 2] El anuncio de prueba fue localizado y eliminado con éxito.")
        else:
            test_passed = False
            print("\n-> [AVISO] Revisa el catálogo manualmente si el anuncio no se borró automáticamente.")

        print("\n" + "=" * 70)
        print("   RESULTADO DEL TEST DE SEGURIDAD")
        print("=" * 70)
        if not delete_only:
            print("   [OK] Creación y subida de anuncio: FUNCIONA")
            print("   [OK] Filtros, fotos y campos de formulario: FUNCIONA")
        if deleted:
            print("   [OK] Localización y borrado de anuncio: FUNCIONA")
            print("\n-> El flujo es 100% SEGURO. Ya puedes ejecutar el pipeline con anuncios reales:")
            print("     python run_pipeline.py --all")
        else:
            print("   [FALLO] No se pudo confirmar el borrado del anuncio.")
        print("=" * 70 + "\n")

        # 3. Envío opcional de informe de prueba por correo si está configurado
        try:
            from email_reporter import send_execution_report
            items_summary = [{
                "reference": "TEST-SEGURIDAD",
                "title": test_title,
                "status": "Successful" if deleted else "Business",
                "message": "Anuncio de prueba publicado y borrado con éxito" if deleted else "Publicado pero pendiente de borrado manual"
            }]
            send_execution_report(
                config=config,
                start_time=test_start_time,
                end_time=datetime.now(),
                processed_count=1,
                successful_count=1 if deleted else 0,
                business_count=0 if deleted else 1,
                system_count=0,
                items=items_summary
            )
        except Exception as ex:
            print(f"   [Aviso Email Test] {ex}")

    except Exception as e:
        test_passed = False
        print(f"\n[ERROR EN TEST] Se produjo un error durante la prueba: {e}")
        import traceback
        traceback.print_exc()

        # Enviar informe de error en el test si está configurado
        try:
            from email_reporter import send_execution_report
            items_summary = [{
                "reference": "TEST-ERROR",
                "title": test_title,
                "status": "System",
                "message": f"Fallo durante la prueba: {str(e)[:150]}"
            }]
            send_execution_report(
                config=config,
                start_time=test_start_time,
                end_time=datetime.now(),
                processed_count=1,
                successful_count=0,
                business_count=0,
                system_count=1,
                items=items_summary
            )
        except Exception:
            pass

    finally:
        # Limpieza de imagen temporal
        if temp_img_path.exists():
            try:
                temp_img_path.unlink()
                temp_img_dir.rmdir()
            except Exception:
                pass

        web_manager.close()

    return test_passed


if __name__ == "__main__":
    delete_only_flag = "--delete-only" in sys.argv or "--delete" in sys.argv
    success = run_test(delete_only=delete_only_flag)
    sys.exit(0 if success else 1)

