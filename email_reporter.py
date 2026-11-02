"""
================================================================================
    Módulo de Envío de Informes por Correo Electrónico (GM_01)
================================================================================
Genera y envía un informe detallado en formato HTML y texto plano al finalizar
la ejecución del pipeline de anuncios en Wallapop.
"""

import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_env_file() -> Dict[str, str]:
    """
    Busca y carga variables desde archivos .env en local_performer/.env o en la raíz.
    Limpia comillas (", '), comas finales (,), espacios y comentarios (#).
    Inyecta los valores en os.environ y devuelve el diccionario cargado.
    """
    loaded = {}
    base_dir = Path(__file__).resolve().parent
    search_paths = [
        base_dir / "local_performer" / ".env",
        base_dir / ".env",
        Path.cwd() / "local_performer" / ".env",
        Path.cwd() / ".env"
    ]

    for p in search_paths:
        if p.exists() and p.is_file():
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            key, val = line.split("=", 1)
                            key = key.strip().lower()
                            val = val.strip()

                            # Quitar comas o puntos y coma finales (ej: "val", o "val";)
                            val = val.rstrip(",;").strip()

                            # Quitar comillas exteriores si las tiene (simples o dobles)
                            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                                val = val[1:-1].strip()

                            # Volver a limpiar comas residuales por si estaban dentro de las comillas
                            val = val.rstrip(",;").strip()

                            loaded[key] = val
                            os.environ[key] = val
                            os.environ[key.upper()] = val
            except Exception as e:
                print(f"   [Aviso .env] Error leyendo {p}: {e}")

    return loaded


# Cargar automáticamente al importar el módulo
load_env_file()


def build_html_report(
    start_time: datetime,
    end_time: datetime,
    processed_count: int,
    successful_count: int,
    business_count: int,
    system_count: int,
    items: List[Dict[str, Any]]
) -> str:
    """Construye un correo HTML moderno, responsive y profesional."""
    duration = end_time - start_time
    duration_str = str(duration).split(".")[0]  # HH:MM:SS
    has_errors = (business_count > 0 or system_count > 0)

    header_gradient = "linear-gradient(135deg, #dc3545 0%, #a71d2a 100%)" if has_errors else "linear-gradient(135deg, #13c1ac 0%, #0c8a7b 100%)"
    header_subtitle = "⚠️ Ejecución Detenida por Seguridad tras Error" if has_errors else "Pipeline de Gestión de Anuncios Wallapop (GM_01)"

    alert_banner = ""
    if has_errors:
        alert_banner = """
        <div style="background-color: #f8d7da; border-left: 4px solid #dc3545; border-radius: 4px; padding: 12px 16px; margin-bottom: 20px; color: #721c24; font-size: 13px;">
            <strong>⚠️ Parada de Seguridad Activada:</strong> Se ha detectado un error durante el procesamiento. Para evitar publicaciones duplicadas o pérdida de datos, la ejecución se ha detenido inmediatamente. Los anuncios restantes han quedado pendientes en la cola.
        </div>
        """

    # Generar filas de la tabla de anuncios
    table_rows_html = ""
    if items:
        for idx, it in enumerate(items, 1):
            st = it.get("status", "Unknown")
            if st == "Successful":
                badge_color = "#28a745"
                badge_text = "ÉXITO"
            elif st == "Business":
                badge_color = "#ffc107"
                badge_text = "NEGOCIO"
            else:
                badge_color = "#dc3545"
                badge_text = "ERROR"

            title = it.get("title", "Sin título")
            ref = it.get("reference", f"#{idx}")
            msg = it.get("message", "-")
            url = it.get("item_url", "")

            title_cell = f"<a href='{url}' style='color: #007bff; text-decoration: none;'>{title}</a>" if url else title

            table_rows_html += f"""
            <tr style="border-bottom: 1px solid #e9ecef;">
                <td style="padding: 10px 12px; font-weight: bold; color: #495057;">#{idx}</td>
                <td style="padding: 10px 12px; color: #6c757d; font-family: monospace;">{ref}</td>
                <td style="padding: 10px 12px; color: #212529;">{title_cell}</td>
                <td style="padding: 10px 12px; text-align: center;">
                    <span style="background-color: {badge_color}; color: #ffffff; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">
                        {badge_text}
                    </span>
                </td>
                <td style="padding: 10px 12px; color: #495057; font-size: 13px;">{msg}</td>
            </tr>
            """
    else:
        table_rows_html = """
        <tr>
            <td colspan="5" style="padding: 20px; text-align: center; color: #6c757d;">
                No se procesaron transacciones en esta sesión.
            </td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Informe de Ejecución Wallapop</title>
    </head>
    <body style="margin: 0; padding: 0; background-color: #f4f6f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f4f6f9; padding: 25px 0;">
            <tr>
                <td align="center">
                    <table width="680" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden;">
                        
                        <!-- Header -->
                        <tr>
                            <td style="background: {header_gradient}; padding: 30px 35px; color: #ffffff;">
                                <h1 style="margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.5px;">
                                    Informe de Ejecución RPA
                                </h1>
                                <p style="margin: 6px 0 0 0; font-size: 14px; opacity: 0.95;">
                                    {header_subtitle}
                                </p>
                            </td>
                        </tr>

                        <!-- Metadata resumen -->
                        <tr>
                            <td style="padding: 25px 35px 15px 35px;">
                                {alert_banner}
                                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 20px;">
                                    <tr>
                                        <td style="font-size: 13px; color: #6c757d;">
                                            <strong>Inicio:</strong> {start_time.strftime("%d/%m/%Y %H:%M:%S")}<br>
                                            <strong>Fin:</strong> {end_time.strftime("%d/%m/%Y %H:%M:%S")}
                                        </td>
                                        <td align="right" style="font-size: 13px; color: #6c757d;">
                                            <strong>Duración total:</strong> {duration_str}<br>
                                            <strong>Fecha:</strong> {datetime.now().strftime("%d/%m/%Y")}
                                        </td>
                                    </tr>
                                </table>

                                <!-- Tarjetas de Métricas -->
                                <table width="100%" cellpadding="0" cellspacing="10" style="margin-bottom: 25px;">
                                    <tr>
                                        <td width="25%" align="center" style="background-color: #f8f9fa; border-radius: 6px; padding: 15px; border-left: 4px solid #007bff;">
                                            <div style="font-size: 24px; font-weight: 700; color: #212529;">{processed_count}</div>
                                            <div style="font-size: 11px; text-transform: uppercase; color: #6c757d; font-weight: bold; margin-top: 4px;">Total</div>
                                        </td>
                                        <td width="25%" align="center" style="background-color: #f8f9fa; border-radius: 6px; padding: 15px; border-left: 4px solid #28a745;">
                                            <div style="font-size: 24px; font-weight: 700; color: #28a745;">{successful_count}</div>
                                            <div style="font-size: 11px; text-transform: uppercase; color: #6c757d; font-weight: bold; margin-top: 4px;">Exitosos</div>
                                        </td>
                                        <td width="25%" align="center" style="background-color: #f8f9fa; border-radius: 6px; padding: 15px; border-left: 4px solid #ffc107;">
                                            <div style="font-size: 24px; font-weight: 700; color: #d39e00;">{business_count}</div>
                                            <div style="font-size: 11px; text-transform: uppercase; color: #6c757d; font-weight: bold; margin-top: 4px;">Negocio</div>
                                        </td>
                                        <td width="25%" align="center" style="background-color: #f8f9fa; border-radius: 6px; padding: 15px; border-left: 4px solid #dc3545;">
                                            <div style="font-size: 24px; font-weight: 700; color: #dc3545;">{system_count}</div>
                                            <div style="font-size: 11px; text-transform: uppercase; color: #6c757d; font-weight: bold; margin-top: 4px;">Sistema</div>
                                        </td>
                                    </tr>
                                </table>

                                <!-- Título Tabla -->
                                <h3 style="margin: 0 0 12px 0; font-size: 16px; color: #212529; border-bottom: 2px solid #e9ecef; padding-bottom: 8px;">
                                    Detalle de Anuncios Procesados
                                </h3>

                                <!-- Tabla -->
                                <table width="100%" cellpadding="0" cellspacing="0" style="font-size: 13px; text-align: left; border-collapse: collapse; margin-bottom: 20px;">
                                    <thead>
                                        <tr style="background-color: #f1f3f5; border-bottom: 2px solid #dee2e6;">
                                            <th style="padding: 10px 12px; color: #495057;">#</th>
                                            <th style="padding: 10px 12px; color: #495057;">Referencia</th>
                                            <th style="padding: 10px 12px; color: #495057;">Título del Anuncio</th>
                                            <th style="padding: 10px 12px; color: #495057; text-align: center;">Estado</th>
                                            <th style="padding: 10px 12px; color: #495057;">Detalles</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {table_rows_html}
                                    </tbody>
                                </table>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="background-color: #f8f9fa; padding: 20px 35px; border-top: 1px solid #e9ecef; font-size: 12px; color: #6c757d; text-align: center;">
                                Este mensaje ha sido generado automáticamente por el sistema <strong>Antigravity RPA (GM_01)</strong>.<br>
                                Por favor no responda directamente a este correo si proviene de un remitente no atendido.
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    return html


def build_text_report(
    start_time: datetime,
    end_time: datetime,
    processed_count: int,
    successful_count: int,
    business_count: int,
    system_count: int,
    items: List[Dict[str, Any]]
) -> str:
    """Construye un informe en texto plano como respaldo."""
    duration = end_time - start_time
    duration_str = str(duration).split(".")[0]

    lines = [
        "=" * 70,
        "   INFORME DE EJECUCIÓN RPA WALLAPOP (GM_01)",
        "=" * 70,
        f"Inicio    : {start_time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Fin       : {end_time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Duración  : {duration_str}",
        "-" * 70,
        "RESUMEN DE RESULTADOS:",
        f"  Total procesadas : {processed_count}",
        f"  Exitosas         : {successful_count}",
        f"  Errores Negocio  : {business_count}",
        f"  Errores Sistema  : {system_count}",
        "=" * 70,
        "DETALLE DE TRANSACCIONES:",
    ]

    for idx, it in enumerate(items, 1):
        lines.append(f"\n#{idx} | Ref: {it.get('reference', 'N/A')} | Estado: {it.get('status', 'N/A')}")
        lines.append(f"   Título  : {it.get('title', 'N/A')}")
        lines.append(f"   Mensaje : {it.get('message', 'N/A')}")

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)


def send_execution_report(
    config: Dict[str, Any],
    start_time: datetime,
    end_time: datetime,
    processed_count: int,
    successful_count: int,
    business_count: int,
    system_count: int,
    items: List[Dict[str, Any]]
) -> bool:
    """
    Lee la configuración de 'email_report' y envía el correo con el informe final.
    Devuelve True si se envió correctamente, False en caso contrario.
    """
    email_cfg = config.get("email_report", {})
    if not isinstance(email_cfg, dict):
        email_cfg = {}

    # Verificar si está habilitado
    enabled = email_cfg.get("enabled", True)
    if not enabled:
        print("   [Email] Envío de informe por correo desactivado en config ('enabled': false).")
        return False

    # Obtener dirección de destino
    recipient = (
        email_cfg.get("recipient_email")
        or email_cfg.get("to_email")
        or email_cfg.get("email")
        or ""
    ).strip()

    if not recipient:
        print("   [Email] No se especificó 'recipient_email' en config.json. Se omite el envío del informe.")
        return False

    # Servidor SMTP y credenciales
    smtp_server = email_cfg.get("smtp_server", "smtp.gmail.com")
    smtp_port = int(email_cfg.get("smtp_port", 587))
    use_tls = email_cfg.get("use_tls", True)
    use_ssl = email_cfg.get("use_ssl", False) or smtp_port == 465

    # Recargar variables de entorno desde .env por si cambiaron dinámicamente
    load_env_file()

    # Remitente: buscar en .env, config o usar recipient como fallback
    env_sender = (
        os.environ.get("sender_email")
        or os.environ.get("SENDER_EMAIL")
        or os.environ.get("EMAIL_USER")
        or os.environ.get("SMTP_USER")
        or ""
    ).strip()

    cfg_sender = (
        email_cfg.get("sender_email")
        or email_cfg.get("from_email")
        or ""
    ).strip()

    # Descartar placeholders genéricos
    if cfg_sender.lower() in ("tucorreo@gmail.com", "tucorreo@ejemplo.com", "example@gmail.com"):
        cfg_sender = ""

    sender = env_sender or cfg_sender or recipient

    # Contraseña: de .env, variable de entorno o config
    password = (
        os.environ.get("sender_password")
        or os.environ.get("SENDER_PASSWORD")
        or os.environ.get("EMAIL_PASSWORD")
        or os.environ.get("SMTP_PASSWORD")
        or email_cfg.get("sender_password")
        or email_cfg.get("password")
        or ""
    ).strip()

    has_errors = (business_count > 0 or system_count > 0)
    if has_errors:
        subject = f"⚠️ ALERTA: Ejecución Wallapop Detenida por Error ({successful_count} OK, {business_count + system_count} Error) - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    else:
        subject = f"✅ Éxito: Informe de Ejecución Wallapop ({successful_count}/{processed_count} Anuncios) - {datetime.now().strftime('%d/%m/%Y')}"

    print(f"\n-> Preparando envío de informe por correo electrónico a: {recipient}...")

    # Construir contenidos
    html_content = build_html_report(
        start_time=start_time,
        end_time=end_time,
        processed_count=processed_count,
        successful_count=successful_count,
        business_count=business_count,
        system_count=system_count,
        items=items
    )
    text_content = build_text_report(
        start_time=start_time,
        end_time=end_time,
        processed_count=processed_count,
        successful_count=successful_count,
        business_count=business_count,
        system_count=system_count,
        items=items
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=25)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=25)
            if use_tls:
                server.starttls()

        if password:
            clean_password = password.replace(" ", "")
            try:
                server.login(sender, clean_password)
            except Exception as login_err:
                if clean_password != password:
                    # Reintentar con la contraseña original si el servidor la requiriese con espacios
                    server.login(sender, password)
                else:
                    raise login_err

        server.sendmail(sender, [recipient], msg.as_string())
        server.quit()

        print(f"   [OK - Email] ¡Informe de ejecución enviado con éxito a {recipient}!")
        return True

    except Exception as e:
        print(f"   [Aviso Email] No se pudo enviar el correo a {recipient}: {e}")
        print("   [Tip Email] Si usas Gmail, recuerda utilizar una 'Contraseña de Aplicación' de 16 letras, no tu contraseña habitual.")
        return False

