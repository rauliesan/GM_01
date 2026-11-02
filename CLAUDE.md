# GM_01 - Contexto y Guía de Desarrollo para Agentes de IA

Proyecto de automatización transaccional local (**Python + Playwright + SQLite + REFramework**) para gestionar, filtrar y republicar anuncios de Wallapop que contengan la palabra clave **`Despiece`** en el título, con envío automático de informes por correo electrónico.

---

## 🏗️ Mapa de Archivos y Componentes

- **`local_queue.py`**: Gestor de colas transaccionales SQLite (`local_queue.db`) con soporte ACID y estados (`New`, `InProgress`, `Successful`, `Business`, `Retried`, `System`, `Abandoned`).
- **`browser_utils.py`**: Utilidades compartidas para inicializar Google Chrome con perfil persistente (`chrome_profile`), evasión anti-detección (`navigator.webdriver` indefinido), banners de cookies y validación de sesión.
- **`email_reporter.py`**: Generador de informes de ejecución en HTML responsive y texto plano, con lectura de credenciales desde `.env` (`local_performer/.env`), sanitización de contraseñas de aplicación Google y envío SMTP/STARTTLS/SSL al correo configurado.
- **`login_wallapop.py`**: Asistente interactivo para inicio de sesión único en Wallapop (`python run_pipeline.py --login`).
- **`test_flow.py`**: Prueba de seguridad integral que crea un anuncio de muestra, lo publica con todos los parámetros configurados y a continuación lo elimina (`python run_pipeline.py --test` y `--test-delete`). Retorna código 0 en éxito y 1 en fallo, con envío de informe por correo.
- **`local_dispatcher/`**: Módulo de extracción y encolado (`main.py`, `config.json`).
- **`local_performer/`**: Módulo consumidor transaccional Playwright (`main.py`, `web_actions.py`, `config.json`, `exceptions.py`, `.env`).
- **`run_pipeline.py`**: CLI unificado con verificación previa obligatoria (Pre-flight test) antes de `--all` y `--performer`, y flag `--skip-test` opcional.
- **`requirements.txt`**: Dependencias pip (`playwright>=1.40.0`, `pillow>=10.0.0`).
- **`README.md`**: Manual de usuario y documentación funcional completa.

---

## 🔍 Reglas de Calidad para Selectores en Wallapop (Stencil + Angular)

Al modificar o ampliar la lógica web en `local_performer/web_actions.py`, seguir estrictamente estas reglas:

1. **Evitar IDs estáticos en formularios:**  
   Wallapop genera atributos `id` y `name` aleatorios con formato UUID (ej. `3bd9d279-...`).  
   - ❌ *Incorrecto:* `input#title`, `input#price_amount`
   - ✅ *Correcto:* `div.inputWrapper:has(label:has-text('Título')) input:visible` o `page.get_by_label('Título')`

2. **Filtro estricto de visibilidad (`:visible`) y elemento activo (`.last`):**  
   En la pantalla inicial de verticales existen tarjetas ocultas con textos como *"Motor y accesorios"*.  
   Al interactuar con menús y subcategorías, utilizar siempre `:visible` y `.last` para interactuar con la capa activa actual y nunca con elementos residuales del DOM.

3. **Prevención de oscilaciones de scroll en botones 'Continuar':**  
   Wallapop utiliza barras inferiores adhesivas (*sticky*). Ejecutar `scroll_into_view_if_needed()` en botones adheridos puede provocar bucles de oscilación de scroll.  
   - Usar `btn.click(force=True)` con respaldo de `page.keyboard.press("Enter")` y clic por coordenadas de ratón.

4. **Detección y Cierre Reactivo de Modales:**  
   No usar pausas ciegas (`wait_for_timeout` largos) para esperar pop-ups de éxito o de upsell post-publicación.  
   - Usar `locator("button.dialog__header__sideButton, button[aria-label='Close']").wait_for(state="visible", timeout=3000)`.  
   - Disparar eventos nativos JS (`pointerdown`, `mousedown`, `click`) en caliente.  
   - Como respaldo definitivo, si un diálogo o backdrop no se cierra, neutralizarlo eliminándolo del DOM (`m.remove()`).

5. **Borrado Seguro con Doble Estrategia:**  
   - Estrategia 1: Buscar tarjeta por texto (`:has-text('...')`), pulsar el menú de 3 puntos (kebab), elegir motivo *"Ya no quiero venderlo"* y confirmar.  
   - Estrategia 2 (Respaldo): Si el menú de la tarjeta no abre la opción, hacer clic en el enlace para entrar a la ficha del anuncio (`/item/...`) y ejecutar el borrado desde allí.  
   - Si no se confirma el borrado, lanzar `SystemException` para detener el proceso antes de tocar anuncios reales.

---

## 🤖 Ciclo Transaccional y Errores (REFramework)

- **`BusinessException(code="SL-02", message="...")`**:
  - Excepción funcional (ej: fotos no extraíbles, URL vacía).
  - Pasa a estado `Business`. **NO se reintenta**.
- **`SystemException(message="...", details="...")`**:
  - Excepción técnica (timeouts, desconexión, error en borrado).
  - Pasa a `Retried` si `retry_count < max_retries`; si se agotan, pasa a `System`.
  - Captura de pantalla automática en `screenshots/error_item_<id>.png`.

---

## 🕹️ Comandos CLI

```powershell
python run_pipeline.py --login        # Autenticación inicial en Wallapop
python run_pipeline.py --test         # Test completo (subida + borrado automático)
python run_pipeline.py --test-delete  # Test solo de borrado de anuncio existente
python run_pipeline.py --dispatcher   # Solo escaneo y encolado (sin tocar anuncios)
python run_pipeline.py --list         # Lista detallada de la cola
python run_pipeline.py --performer    # Procesa y republica los anuncios en cola
python run_pipeline.py --all          # Flujo completo (Dispatcher + Performer + Email)
python run_pipeline.py --status       # Resumen numérico de la cola
python run_pipeline.py --clear        # Limpiar pendientes (Abandoned)
```

---

## 💻 Despliegue en un Nuevo Ordenador (Auto-Bootstrap)

1. **No transferir nunca:** `chrome_profile/`, `local_queue.db`, `screenshots/`, `temp_media/`, `venv/`, `.venv/` (ya excluidos en `.gitignore`).
2. **Credenciales:** Archivo `local_performer/.env` con `sender_email` y `sender_password`.
3. **Auto-Bootstrap:** Al ejecutar cualquier comando (ej. `python run_pipeline.py --login`), el script detecta si falta el `venv` o dependencias y las instala automáticamente sin intervención manual.
4. **Primera ejecución (Obligatoria):** `python run_pipeline.py --login` para autenticarse manualmente en Wallapop una sola vez y generar el `chrome_profile` local de ese equipo.
5. **Ejecución normal:** `python run_pipeline.py --all` (incluye Pre-flight Test automático previo y creación de base de datos SQLite si no existía).

---

## 🔒 Buenas Prácticas Obligatorias

1. **Rutas 100% Relativas:** Prohibido el uso de rutas absolutas locales (`C:\Users\...`). Resolver todo mediante `Path(__file__).resolve().parent`.
2. **Seguridad de Credenciales:** La sesión de Wallapop se almacena exclusivamente en `chrome_profile/` (excluida por `.gitignore`). Las contraseñas de email pueden pasarse por `config.json` o variable de entorno `SMTP_PASSWORD`.
3. **Encoding de Consola:** Todos los puntos de entrada incluyen `sys.stdout.reconfigure(encoding="utf-8")` para evitar errores `UnicodeEncodeError` en terminales de Windows.
