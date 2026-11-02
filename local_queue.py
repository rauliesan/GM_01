"""
================================================================================
    LocalQueue - Gestor de Colas Transaccionales basado en SQLite (100% Offline)
================================================================================
Emula fielmente el comportamiento de colas transaccionales tipo REFramework:
- Estados: New, InProgress, Successful, Business, Retried, System, Abandoned.
- Atomicidad: Extracción segura de transacciones (pop) con bloqueo SQLite.
- Reintentos: Control automático de retry_count y max_retries en errores técnicos.
- Sin dependencias externas: Funciona con la librería estándar de Python (sqlite3).
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class LocalQueue:
    def __init__(self, db_path: str = "local_queue.db"):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Crea la estructura de tablas e índices si no existen."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS queue_items (
                    id TEXT PRIMARY KEY,
                    queue_name TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'New',
                    priority TEXT NOT NULL DEFAULT 'Medium',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    output_code TEXT,
                    output_message TEXT,
                    error_details TEXT,
                    job_id TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON queue_items (queue_name, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_reference ON queue_items (queue_name, reference)")

    def push_item(
        self,
        queue_name: str,
        reference: str,
        payload: Dict[str, Any],
        priority: str = "Medium",
        max_retries: int = 3,
        job_id: Optional[str] = None
    ) -> str:
        """Inserta un nuevo ítem en estado 'New'."""
        item_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        payload_json = json.dumps(payload, ensure_ascii=False)

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO queue_items (
                    id, queue_name, reference, payload, status, priority,
                    retry_count, max_retries, job_id, created_at
                ) VALUES (?, ?, ?, ?, 'New', ?, 0, ?, ?, ?)
            """, (item_id, queue_name, reference, payload_json, priority, max_retries, job_id, created_at))
        return item_id

    def push_items_bulk(
        self,
        queue_name: str,
        items: List[Dict[str, Any]],
        job_id: Optional[str] = None
    ) -> List[str]:
        """Inserta una lista de ítems de forma atómica en una sola transacción."""
        item_ids = []
        created_at = datetime.now().isoformat()
        rows = []

        for it in items:
            iid = str(uuid.uuid4())
            item_ids.append(iid)
            ref = str(it.get("reference", f"REF-{iid[:8]}"))
            payload = it.get("payload", it)
            payload_json = json.dumps(payload, ensure_ascii=False)
            priority = it.get("priority", "Medium")
            max_retries = int(it.get("max_retries", 3))

            rows.append((
                iid, queue_name, ref, payload_json, 'New', priority,
                0, max_retries, job_id, created_at
            ))

        with self._get_connection() as conn:
            conn.executemany("""
                INSERT INTO queue_items (
                    id, queue_name, reference, payload, status, priority,
                    retry_count, max_retries, job_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

        return item_ids

    def pop_next_item(self, queue_name: str, job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Extrae de forma atómica el siguiente ítem pendiente ('New') según prioridad y fecha.
        Lo marca inmediatamente como 'InProgress' con su timestamp de inicio.
        """
        priority_order = "CASE priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 3 ELSE 4 END"
        now_str = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # BEGIN IMMEDIATE asegura bloqueo exclusivo de escritura
            cursor.execute("BEGIN IMMEDIATE")

            cursor.execute(f"""
                SELECT id, queue_name, reference, payload, status, priority,
                       retry_count, max_retries, job_id, created_at
                FROM queue_items
                WHERE queue_name = ? AND status = 'New'
                ORDER BY {priority_order}, created_at ASC
                LIMIT 1
            """, (queue_name,))
            row = cursor.fetchone()

            if not row:
                conn.commit()
                return None

            item_id = row["id"]
            cursor.execute("""
                UPDATE queue_items
                SET status = 'InProgress', started_at = ?, job_id = COALESCE(?, job_id)
                WHERE id = ?
            """, (now_str, job_id, item_id))
            conn.commit()

            return {
                "id": row["id"],
                "queue_name": row["queue_name"],
                "reference": row["reference"],
                "payload": json.loads(row["payload"]),
                "status": "InProgress",
                "priority": row["priority"],
                "retry_count": row["retry_count"],
                "max_retries": row["max_retries"],
                "job_id": job_id or row["job_id"],
                "created_at": row["created_at"],
                "started_at": now_str
            }

    def set_successful(
        self,
        item_id: str,
        output_code: str = "SL-01",
        output_message: str = "Transacción completada con éxito"
    ):
        """Marca la transacción como finalizada correctamente."""
        ended_at = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE queue_items
                SET status = 'Successful', output_code = ?, output_message = ?, ended_at = ?
                WHERE id = ?
            """, (output_code, output_message, ended_at, item_id))

    def set_business_error(
        self,
        item_id: str,
        output_code: str = "SL-02",
        output_message: str = "Error funcional de negocio",
        error_details: str = ""
    ):
        """Marca la transacción como error de negocio (no reintentable)."""
        ended_at = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE queue_items
                SET status = 'Business', output_code = ?, output_message = ?,
                    error_details = ?, ended_at = ?
                WHERE id = ?
            """, (output_code, output_message, error_details, ended_at, item_id))

    def set_system_error(
        self,
        item_id: str,
        error_details: str,
        output_code: str = "SL-00",
        output_message: str = "Error técnico del sistema"
    ) -> str:
        """
        Gestiona un error técnico. Si quedan reintentos disponibles, marca como 'Retried'
        y vuelve a poner el ítem en 'New' incrementando retry_count. Si se superan, marca 'System'.
        Devuelve el estado resultante ('Retried' o 'System').
        """
        ended_at = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT retry_count, max_retries FROM queue_items WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                return "System"

            current_retry = row["retry_count"]
            max_retries = row["max_retries"]

            if current_retry + 1 < max_retries:
                # Reintento pendiente: vuelve a 'New' para la siguiente ejecución
                new_retry = current_retry + 1
                cursor.execute("""
                    UPDATE queue_items
                    SET status = 'New', retry_count = ?, output_code = 'SL-RETRIED',
                        output_message = 'Reintento programado', error_details = ?, ended_at = ?
                    WHERE id = ?
                """, (new_retry, error_details, ended_at, item_id))
                conn.commit()
                return "Retried"
            else:
                # Agotados los reintentos: fallo técnico definitivo
                cursor.execute("""
                    UPDATE queue_items
                    SET status = 'System', output_code = ?, output_message = ?,
                        error_details = ?, ended_at = ?
                    WHERE id = ?
                """, (output_code, output_message, error_details, ended_at, item_id))
                conn.commit()
                return "System"

    def recover_stale_items(self, queue_name: str) -> int:
        """
        Recupera ítems que quedaron en 'InProgress' (por ejemplo si el proceso se detuvo
        inesperadamente) y los devuelve a 'New' para que no queden bloqueados.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE queue_items
                SET status = 'New', started_at = NULL
                WHERE queue_name = ? AND status = 'InProgress'
            """, (queue_name,))
            conn.commit()
            return cursor.rowcount

    def clear_queue(self, queue_name: str) -> int:
        """Pasa los ítems pendientes 'New' a 'Abandoned'."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE queue_items
                SET status = 'Abandoned', ended_at = ?
                WHERE queue_name = ? AND status = 'New'
            """, (datetime.now().isoformat(), queue_name))
            conn.commit()
            return cursor.rowcount

    def get_summary(self, queue_name: Optional[str] = None) -> Dict[str, int]:
        """Devuelve el conteo de ítems agrupados por estado."""
        query = "SELECT status, COUNT(*) as count FROM queue_items"
        params = []
        if queue_name:
            query += " WHERE queue_name = ?"
            params.append(queue_name)
        query += " GROUP BY status"

        summary = {
            "New": 0,
            "InProgress": 0,
            "Successful": 0,
            "Business": 0,
            "Retried": 0,
            "System": 0,
            "Abandoned": 0,
            "Total": 0
        }

        with self._get_connection() as conn:
            for row in conn.execute(query, params):
                st = row["status"]
                cnt = row["count"]
                summary[st] = cnt
                summary["Total"] += cnt

        return summary
