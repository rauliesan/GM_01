"""
================================================================================
    Performer Local - Consumo de Cola y Automatización Web (REFramework)
================================================================================
Consume ítems de la cola local SQLite, ejecuta la lógica Playwright y actualiza
el estado (Successful, Business, Retried, System). 100% Offline y transaccional.
"""

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# Asegurar encoding UTF-8 en consola de Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Permitir importar módulos desde la raíz y desde local_performer
CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from browser_utils import resolve_chrome_profile_dir
from local_queue import LocalQueue
try:
    from exceptions import BusinessException, SystemException
    from web_actions import WebAutomationManager
except ImportError:
    from local_performer.exceptions import BusinessException, SystemException
    from local_performer.web_actions import WebAutomationManager


def load_config(config_path: Path) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_performer():
    config_file = Path(__file__).resolve().parent / "config.json"
    config = load_config(config_file)

    db_path = (Path(__file__).resolve().parent / config["db_path"]).resolve()
    queue_name = config.get("queue_name", "GM01_WallapopQueue")
    headless = config.get("headless", False)
    browser_type = config.get("browser_type", "chromium")
    timeout_ms = config.get("timeout_ms", 30000)
    slow_mo_ms = config.get("slow_mo_ms", 150)
    delay_between_items = config.get("delay_between_items_ms", 2000) / 1000.0
    profile_dir = resolve_chrome_profile_dir(config.get("profile_dir", "system"))
    credential_name = config.get("credential_name", "")

    print("=" * 70)
    print("   PERFORMER LOCAL (GM_01) - PUBLICACIÓN WALLAPOP")
    print("=" * 70)
    print(f"Base de datos : {db_path}")
    print(f"Cola origen   : {queue_name}")
    print(f"Navegador     : {browser_type} (headless={headless}, slow_mo={slow_mo_ms}ms)")
    print(f"Perfil Chrome : {profile_dir}")
    print("-" * 70)

    queue = LocalQueue(db_path=str(db_path))

    # Recuperar posibles ítems que quedaron colgados en InProgress por cortes previos
    recovered = queue.recover_stale_items(queue_name)
    if recovered > 0:
        print(f"-> Se recuperaron {recovered} ítems 'InProgress' y volvieron a 'New'.")

    # Verificar si hay ítems pendientes antes de iniciar navegador o tests
    summary = queue.get_summary(queue_name)
    new_count = summary.get("New", 0)
    if new_count == 0:
        print("\n-> No hay transacciones pendientes ('New') en la cola. Nada que procesar.")
        return

    # 0. PRE-FLIGHT TEST OBLIGATORIO (si no ha sido validado previamente en run_pipeline)
    if config.get("preflight_test", True) and os.environ.get("GM01_PREFLIGHT_PASSED") != "1":
        print("\n" + "=" * 70)
        print("   VERIFICACIÓN PREVIA OBLIGATORIA (PRE-FLIGHT TEST)")
        print("=" * 70)
        print("-> Ejecutando test de seguridad (creación y borrado) antes de procesar anuncios...")
        test_script = ROOT_DIR / "test_flow.py"
        res = subprocess.run([sys.executable, str(test_script)], cwd=str(ROOT_DIR))
        if res.returncode != 0:
            print("\n" + "!" * 70)
            print("   [PARADA DE SEGURIDAD] La prueba previa de seguridad ha fallado.")
            print("   No se procesará ningún anuncio real. Informe enviado por correo.")
            print("!" * 70 + "\n")
            return
        os.environ["GM01_PREFLIGHT_PASSED"] = "1"
        print("-> [OK] Test de seguridad superado con éxito. Iniciando Performer...\n")

    # 1. INITIALIZATION: Iniciar navegador Playwright con sesión persistente
    web_manager = WebAutomationManager(
        headless=headless,
        browser_type=browser_type,
        timeout_ms=timeout_ms,
        slow_mo_ms=slow_mo_ms,
        profile_dir=profile_dir,
        credential_name=credential_name,
        config=config
    )

    try:
        web_manager.start()
    except Exception as e:
        print(f"[FATAL] Error iniciando el navegador Playwright: {e}")
        try:
            from email_reporter import send_execution_report
            send_execution_report(
                config=config,
                start_time=datetime.now(),
                end_time=datetime.now(),
                processed_count=0,
                successful_count=0,
                business_count=0,
                system_count=1,
                items=[{"reference": "INICIO", "title": "Inicio de Sesión / Navegador", "status": "System", "message": str(e)}]
            )
        except Exception:
            pass
        return

    session_start_time = datetime.now()
    execution_items = []
    processed_count = 0
    successful_count = 0
    business_count = 0
    system_count = 0
    stop_on_error = config.get("stop_on_error", True)

    # 2. BUCLE TRANSACCIONAL (GET TRANSACTION -> PROCESS)
    try:
        while True:
            item = queue.pop_next_item(queue_name, job_id="PERFORMER_GM01")
            if not item:
                print("\n-> No hay más ítems pendientes en la cola. Proceso completado con éxito.")
                break

            processed_count += 1
            item_id = item["id"]
            reference = item["reference"]
            payload = item["payload"]
            retry_count = item["retry_count"]

            print("\n" + "=" * 70)
            print(f"Procesando Transacción #{processed_count}")
            print(f"   Referencia  : {reference}")
            print(f"   Título Base : {payload.get('title', 'N/A')}")
            print(f"   URL Anuncio : {payload.get('item_url', 'N/A')}")
            print(f"   UUID Cola   : {item_id}")
            print(f"   Reintento   : {retry_count}")
            print("-" * 70)

            # 3. PROCESS: Ejecutar automatización web
            try:
                result_data = web_manager.execute_transaction_logic(payload)
                msg = f"Éxito: {result_data.get('title', 'Publicado correctamente')}"
                queue.set_successful(item_id, output_code="SL-01", output_message=msg)
                successful_count += 1
                print(f"   [OK - SL-01] {msg}")

                execution_items.append({
                    "reference": reference,
                    "title": result_data.get("title") or payload.get("title", "N/A"),
                    "item_url": payload.get("item_url", ""),
                    "status": "Successful",
                    "message": msg
                })

            except BusinessException as be:
                queue.set_business_error(
                    item_id=item_id,
                    output_code=be.code,
                    output_message=be.message,
                    error_details=be.details
                )
                business_count += 1
                print(f"   [BUSINESS - {be.code}] {be.message}")

                execution_items.append({
                    "reference": reference,
                    "title": payload.get("title", "N/A"),
                    "item_url": payload.get("item_url", ""),
                    "status": "Business",
                    "message": f"[{be.code}] {be.message}"
                })

                if stop_on_error:
                    print("\n" + "!" * 70)
                    print(f"   [PARADA DE SEGURIDAD] Error en {reference}: {be.message}")
                    print("   Deteniendo la ejecución de la cola por completo y enviando informe...")
                    print("!" * 70 + "\n")
                    break

            except Exception as ex:
                err_trace = traceback.format_exc()
                res_status = queue.set_system_error(
                    item_id=item_id,
                    error_details=f"{str(ex)}\n{err_trace}"
                )
                system_count += 1
                if res_status == "Retried":
                    print(f"   [RETRIED] Error técnico. Reintento programado para la siguiente pasada.")
                else:
                    print(f"   [SYSTEM FATAL] Se agotaron los reintentos: {ex}")

                execution_items.append({
                    "reference": reference,
                    "title": payload.get("title", "N/A"),
                    "item_url": payload.get("item_url", ""),
                    "status": "System",
                    "message": f"{str(ex)[:100]}"
                })

                if stop_on_error:
                    print("\n" + "!" * 70)
                    print(f"   [PARADA DE SEGURIDAD] Error técnico en {reference}: {ex}")
                    print("   Deteniendo la ejecución de la cola por completo y enviando informe...")
                    print("!" * 70 + "\n")
                    break

            if delay_between_items > 0:
                print(f"-> Esperando {delay_between_items}s antes del siguiente ítem...")
                time.sleep(delay_between_items)

    finally:
        # 4. END PROCESS: Cerrar aplicativos y resumen
        web_manager.close()

    print("\n" + "=" * 70)
    print("   RESUMEN FINAL DE LA EJECUCIÓN (GM_01)")
    print("=" * 70)
    print(f"Total procesadas en esta sesión : {processed_count}")
    print(f"   - Exitosas (Successful)      : {successful_count}")
    print(f"   - Negocio (Business)         : {business_count}")
    print(f"   - Errores Sistema (System)   : {system_count}")
    print("-" * 70)

    # Estado global acumulado en SQLite
    db_summary = queue.get_summary(queue_name)
    print("ESTADO ACUMULADO EN LA BASE DE DATOS:")
    for status, count in db_summary.items():
        if count > 0:
            print(f"   [{status:12}] : {count}")
    print("=" * 70)

    # 5. Envío de informe por correo electrónico al finalizar
    session_end_time = datetime.now()
    try:
        from email_reporter import send_execution_report
        send_execution_report(
            config=config,
            start_time=session_start_time,
            end_time=session_end_time,
            processed_count=processed_count,
            successful_count=successful_count,
            business_count=business_count,
            system_count=system_count,
            items=execution_items
        )
    except Exception as e:
        print(f"   [Aviso Email] Fallo enviando informe: {e}")


if __name__ == "__main__":
    run_performer()
