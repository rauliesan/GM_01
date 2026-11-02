"""
================================================================================
    Runner Unificado - Pipeline Local Dispatcher & Performer (Playwright + SQLite)
================================================================================
Comandos disponibles:
  python run_pipeline.py --login        # Abre Chrome para iniciar sesión en Wallapop
  python run_pipeline.py --test         # Ejecuta prueba segura: crea anuncio test y luego lo borra
  python run_pipeline.py --all          # Ejecuta Dispatcher y luego Performer
  python run_pipeline.py --dispatcher   # Ejecuta solo el Dispatcher (escanea y encola)
  python run_pipeline.py --performer    # Ejecuta solo el Performer (descarga, borra y sube)
  python run_pipeline.py --status       # Muestra el estado actual de la cola SQLite
  python run_pipeline.py --list         # Lista los últimos ítems registrados
  python run_pipeline.py --clear        # Descarta los ítems pendientes (Abandoned)
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent


def ensure_environment_and_reexec():
    """
    Auto-bootstrap transparente:
    1. Si no se está ejecutando dentro de un entorno virtual (venv):
       - Crea la carpeta 'venv/' si no existe.
       - Re-ejecuta run_pipeline.py usando el ejecutable de Python del venv.
    2. Dentro del venv:
       - Comprueba si las dependencias (playwright, PIL) están instaladas.
       - Si falta alguna, ejecuta automáticamente pip install -r requirements.txt
         y playwright install chromium.
    """
    in_venv = (
        getattr(sys, "base_prefix", sys.prefix) != sys.prefix
        or os.environ.get("VIRTUAL_ENV") is not None
        or os.environ.get("GM01_IN_VENV") == "1"
    )

    venv_dir = ROOT_DIR / "venv"
    if os.name == "nt":
        venv_python = venv_dir / "Scripts" / "python.exe"
    else:
        venv_python = venv_dir / "bin" / "python"

    # Si NO estamos dentro del venv de la carpeta del proyecto
    if not in_venv or Path(sys.executable).resolve() != venv_python.resolve():
        if not venv_python.exists():
            print("\n" + "=" * 70)
            print("   CONFIGURACIÓN AUTOMÁTICA INICIAL (AUTO-BOOTSTRAP)")
            print("=" * 70)
            print("-> Detectada primera ejecución en este equipo.")
            print("-> Creando entorno virtual aislado (venv)...")
            res = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)])
            if res.returncode != 0:
                print(f"[ALERTA] No se pudo crear el venv automáticamente: código {res.returncode}")
                return

        if venv_python.exists():
            # Re-ejecutar el script actual usando el Python del venv
            env_vars = os.environ.copy()
            env_vars["GM01_IN_VENV"] = "1"
            env_vars["VIRTUAL_ENV"] = str(venv_dir)
            result = subprocess.run([str(venv_python)] + sys.argv, env=env_vars)
            sys.exit(result.returncode)

    # Si ya estamos dentro del venv, comprobar e instalar dependencias si faltan
    missing_deps = False
    try:
        import playwright
        import PIL
    except ImportError:
        missing_deps = True

    if missing_deps:
        print("\n" + "=" * 70)
        print("   INSTALACIÓN AUTOMÁTICA DE DEPENDENCIAS EN EL VENV")
        print("=" * 70)
        print("-> Instalando librerías requeridas (Playwright, Pillow)...")
        req_file = ROOT_DIR / "requirements.txt"
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=False)
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req_file)], check=True)
        print("-> Instalando binarios del navegador de Playwright (Chromium)...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        print("-> [OK] Entorno virtual y dependencias listos al 100%.\n" + "=" * 70 + "\n")


# Verificar y asegurar el entorno virtual de forma inmediata antes de cargar librerías
ensure_environment_and_reexec()

from local_queue import LocalQueue

ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "local_queue.db"
DISPATCHER_SCRIPT = ROOT_DIR / "local_dispatcher" / "main.py"
PERFORMER_SCRIPT = ROOT_DIR / "local_performer" / "main.py"
LOGIN_SCRIPT = ROOT_DIR / "login_wallapop.py"
TEST_SCRIPT = ROOT_DIR / "test_flow.py"


def get_queue_name() -> str:
    cfg_path = ROOT_DIR / "local_dispatcher" / "config.json"
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("queue_name", "GM01_WallapopQueue")
        except Exception:
            pass
    return "GM01_WallapopQueue"


def run_script(script_path: Path, env_extra: dict = None):
    print(f"\n>>> Lanzando: {script_path.relative_to(ROOT_DIR)} ...\n")
    run_env = sys.modules["os"].environ.copy()
    if env_extra:
        run_env.update(env_extra)
    res = subprocess.run([sys.executable, str(script_path)], cwd=str(script_path.parent), env=run_env)
    if res.returncode != 0:
        print(f"\n[ALERTA] El script finalizó con código de error {res.returncode}")
        return False
    return True


def run_preflight_check() -> bool:
    """
    Ejecuta la prueba de seguridad previa obligatoria (test_flow.py).
    Garantiza que la subida y el borrado funcionan al 100% antes de tocar anuncios reales.
    Si falla, test_flow.py ya envía el informe de error a raullsdev@gmail.com.
    """
    print("\n" + "=" * 75)
    print("   VERIFICACIÓN PREVIA OBLIGATORIA (PRE-FLIGHT TEST)")
    print("=" * 75)
    print("-> Ejecutando test de seguridad (creación y borrado de prueba)...")
    print("-> Esta prueba verifica que los selectores, el formulario y el borrado están operativos.")
    print("-> Si el test falla, la ejecución REAL se CANCELA de inmediato y se envía el informe.")
    print("-" * 75 + "\n")

    test_ok = run_script(TEST_SCRIPT)
    if not test_ok:
        print("\n" + "!" * 75)
        print("   [PARADA DE SEGURIDAD ACTIVADA]")
        print("   La prueba previa de seguridad ha FALLADO.")
        print("   Por seguridad, NO se procesará ningún anuncio real en la cola.")
        print("   El informe con el detalle del error ya ha sido enviado a tu correo.")
        print("!" * 75 + "\n")
        return False

    print("\n" + "=" * 75)
    print("   [PRE-FLIGHT TEST SUPERADO CON ÉXITO]")
    print("   Formulario, filtros y borrado verificados. Procediendo de forma segura...")
    print("=" * 75 + "\n")
    return True


def show_status():
    queue = LocalQueue(db_path=str(DB_PATH))
    qname = get_queue_name()
    summary = queue.get_summary(qname)

    print("\n" + "=" * 50)
    print(f"ESTADO DE LA COLA LOCAL ({qname})")
    print("=" * 50)
    for st, count in summary.items():
        if st != "Total":
            print(f"  {st:15} : {count}")
    print("-" * 50)
    print(f"  {'TOTAL':15} : {summary['Total']}")
    print("=" * 50 + "\n")


def list_items(limit: int = 20):
    if not DB_PATH.exists():
        print("La base de datos todavía no existe. Ejecuta primero el Dispatcher.")
        return

    qname = get_queue_name()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, queue_name, reference, status, priority, retry_count,
               output_code, output_message, created_at, started_at, ended_at
        FROM queue_items
        WHERE queue_name = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (qname, limit))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print(f"No hay transacciones registradas en la cola '{qname}'.")
        return

    print("\n" + "=" * 105)
    print(f"{'REFERENCIA':<25} | {'ESTADO':<12} | {'SALIDA':<8} | {'REINTENTOS':<10} | {'MENSAJE / RESULTADO':<40}")
    print("-" * 105)
    for r in rows:
        ref = r["reference"][:24]
        st = r["status"]
        code = r["output_code"] or "-"
        retries = r["retry_count"]
        msg = (r["output_message"] or "")[:40]
        print(f"{ref:<25} | {st:<12} | {code:<8} | {retries:<10} | {msg:<40}")
    print("=" * 105 + "\n")


def clear_queue():
    queue = LocalQueue(db_path=str(DB_PATH))
    qname = get_queue_name()
    count = queue.clear_queue(qname)
    print(f"\n-> Se han marcado {count} transacciones pendientes de '{qname}' como 'Abandoned'.\n")


def main():
    parser = argparse.ArgumentParser(description="Runner del pipeline local RPA GM_01 (Playwright + SQLite)")
    parser.add_argument("--login", action="store_true", help="Abre Chrome para iniciar sesión en Wallapop")
    parser.add_argument("--test", "-t", action="store_true", help="Ejecuta prueba de seguridad: crea anuncio test y luego lo borra")
    parser.add_argument("--test-delete", action="store_true", help="Prueba solo el borrado del anuncio de test en el catálogo")
    parser.add_argument("--all", action="store_true", help="Ejecuta Dispatcher y a continuación Performer (con test previo obligatorio)")
    parser.add_argument("--dispatcher", "-d", action="store_true", help="Ejecuta solo el Dispatcher")
    parser.add_argument("--performer", "-p", action="store_true", help="Ejecuta solo el Performer (con test previo obligatorio)")
    parser.add_argument("--skip-test", action="store_true", help="Omite la prueba de seguridad previa obligatoria (no recomendado)")
    parser.add_argument("--status", "-s", action="store_true", help="Muestra el resumen de estados de la cola")
    parser.add_argument("--list", "-l", action="store_true", help="Muestra las últimas transacciones")
    parser.add_argument("--clear", "-c", action="store_true", help="Descarta las transacciones pendientes")

    args = parser.parse_args()

    if args.login:
        run_script(LOGIN_SCRIPT)
    elif args.test:
        run_script(TEST_SCRIPT)
    elif args.test_delete:
        print(f"\n>>> Lanzando borrado de prueba: {TEST_SCRIPT.relative_to(ROOT_DIR)} --delete-only ...\n")
        subprocess.run([sys.executable, str(TEST_SCRIPT), "--delete-only"], cwd=str(TEST_SCRIPT.parent))
    elif args.all:
        if not args.skip_test:
            if not run_preflight_check():
                sys.exit(1)
        # Indicar al performer que la prueba previa ya fue validada con éxito
        if run_script(DISPATCHER_SCRIPT):
            run_script(PERFORMER_SCRIPT, env_extra={"GM01_PREFLIGHT_PASSED": "1"})
        show_status()
    elif args.dispatcher:
        run_script(DISPATCHER_SCRIPT)
        show_status()
    elif args.performer:
        if not args.skip_test:
            if not run_preflight_check():
                sys.exit(1)
        run_script(PERFORMER_SCRIPT, env_extra={"GM01_PREFLIGHT_PASSED": "1"})
        show_status()
    elif args.status:
        show_status()
    elif args.list:
        list_items()
    elif args.clear:
        clear_queue()
    else:
        show_status()
        parser.print_help()


if __name__ == "__main__":
    main()
