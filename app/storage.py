"""SQLite persistence and serialized read-modify-publish transactions."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .solver import solve_configuration

_write_lock = threading.RLock()


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    if db_path != ":memory:":
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        # Some restricted/network filesystems do not support WAL; DELETE still
        # provides correct serialization with BEGIN IMMEDIATE.
        pass
    return conn


def init_db(db_path: str) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS registrations (
                mic_id TEXT PRIMARY KEY,
                backup_frequency_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mics (
                mic_id TEXT PRIMARY KEY,
                position INTEGER NOT NULL,
                name TEXT,
                main_frequency_text TEXT NOT NULL,
                candidates_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS interferences (
                interference_id TEXT PRIMARY KEY,
                position INTEGER NOT NULL,
                name TEXT,
                frequencies_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS versions (
                version INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                trigger_name TEXT NOT NULL,
                setup_name TEXT,
                config_json TEXT NOT NULL,
                result_json TEXT NOT NULL
            );
            """
        )
    finally:
        conn.close()


@contextmanager
def write_transaction(db_path: str) -> Iterator[sqlite3.Connection]:
    # The in-process lock makes concurrent FastAPI threadpool operations
    # deterministic.  BEGIN IMMEDIATE additionally serializes multiple
    # processes against SQLite's write lock.
    with _write_lock:
        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else row["value"]


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def _assign_identifiers(items: list[Any], prefix: str, id_attr: str) -> list[str]:
    assigned: list[str] = []
    used: set[str] = set()
    for index, item in enumerate(items):
        provided = getattr(item, id_attr, None)
        identifier = str(provided).strip() if provided is not None else f"{prefix}{index + 1}"
        if not identifier:
            identifier = f"{prefix}{index + 1}"
        if identifier in used:
            raise ApiError(
                422,
                "duplicate_identifier",
                f"重复的 ID：{identifier}",
                {"identifier": identifier},
            )
        used.add(identifier)
        assigned.append(identifier)
    return assigned


def _resolve_mic_id(conn: sqlite3.Connection, item: Any) -> str:
    rows = conn.execute("SELECT mic_id FROM mics ORDER BY position ASC").fetchall()
    ids = [row["mic_id"] for row in rows]
    if not ids:
        raise ApiError(409, "no_configuration", "尚未录入麦和主频，请先提交配频设置。")

    mic_id = getattr(item, "mic_id", None)
    if mic_id is not None:
        ref = str(mic_id).strip()
        if ref in ids:
            return ref

        numeric: Optional[int]
        try:
            numeric = int(ref)
        except (TypeError, ValueError):
            numeric = None

        if numeric is not None:
            # Exact string IDs, including client-supplied "0"/"1", were
            # handled above.  Then support common one-based mic numbers and
            # explicit zero-based array positions.
            if 1 <= numeric <= len(ids):
                return ids[numeric - 1]
            if 0 <= numeric < len(ids):
                return ids[numeric]

        raise ApiError(
            404,
            "mic_not_found",
            f"找不到麦：{ref}",
            {"mic_id": ref, "available_mic_ids": ids},
        )

    mic_index = getattr(item, "mic_index", None)
    if mic_index is not None:
        if mic_index >= len(ids):
            raise ApiError(
                404,
                "mic_not_found",
                f"mic_index={mic_index} 超出范围（共 {len(ids)} 支麦）。",
                {"available_mic_ids": ids},
            )
        return ids[mic_index]

    mic_number = getattr(item, "mic_number", None)
    if mic_number is not None:
        if mic_number < 1 or mic_number > len(ids):
            raise ApiError(
                404,
                "mic_not_found",
                f"mic_number={mic_number} 超出范围（共 {len(ids)} 支麦）。",
                {"available_mic_ids": ids},
            )
        return ids[mic_number - 1]

    raise ApiError(
        422,
        "missing_mic_reference",
        "必须提供 mic_id、mic_index 或 mic_number。",
        {"available_mic_ids": ids},
    )


def _publish_version(
    conn: sqlite3.Connection, trigger_name: str, setup_name: Optional[str] = None
) -> dict[str, Any]:
    mic_rows = conn.execute(
        """
        SELECT m.mic_id, m.position, m.name, m.main_frequency_text,
               m.candidates_json, r.backup_frequency_text AS installed_backup_text,
               r.updated_at AS registered_at
        FROM mics m
        LEFT JOIN registrations r ON r.mic_id = m.mic_id
        ORDER BY m.position ASC, m.rowid ASC
        """
    ).fetchall()

    mics: list[dict[str, Any]] = []
    for row in mic_rows:
        installed_text = row["installed_backup_text"]
        mics.append(
            {
                "mic_id": row["mic_id"],
                "position": row["position"],
                "name": row["name"],
                "main_frequency": int(row["main_frequency_text"]),
                "backup_candidates": json.loads(row["candidates_json"]),
                "installed_backup": (
                    int(installed_text) if installed_text is not None else None
                ),
                "registered_at": row["registered_at"],
            }
        )

    interference_rows = conn.execute(
        "SELECT * FROM interferences ORDER BY position ASC, rowid ASC"
    ).fetchall()
    interferences = [
        {
            "interference_id": row["interference_id"],
            "position": row["position"],
            "name": row["name"],
            "occupied_frequencies": json.loads(row["frequencies_json"]),
        }
        for row in interference_rows
    ]

    if setup_name is None:
        setup_name = _meta_get(conn, "setup_name")
    else:
        _meta_set(conn, "setup_name", setup_name)

    configuration = {
        "name": setup_name,
        "mics": mics,
        "interferences": interferences,
    }
    result = solve_configuration(configuration)
    now = utc_now()

    cursor = conn.execute(
        """
        INSERT INTO versions(
            created_at, trigger_name, setup_name, config_json, result_json
        ) VALUES(?, ?, ?, ?, ?)
        """,
        (
            now,
            trigger_name,
            setup_name,
            json.dumps(configuration, ensure_ascii=False, separators=(",", ":")),
            "{}",
        ),
    )
    version = int(cursor.lastrowid)
    result["effective_version"] = version
    result["effective_at"] = now
    result["trigger"] = trigger_name

    conn.execute(
        "UPDATE versions SET result_json=? WHERE version=?",
        (json.dumps(result, ensure_ascii=False, separators=(",", ":")), version),
    )
    _meta_set(conn, "current_version", str(version))

    return {
        "effective_version": version,
        "effective_at": now,
        "trigger": trigger_name,
        "configuration": configuration,
        **result,
    }


def save_configuration(db_path: str, payload: Any) -> dict[str, Any]:
    mic_ids = _assign_identifiers(payload.mics, "m", "mic_id")
    interference_ids = _assign_identifiers(
        payload.interferences, "i", "interference_id"
    )

    with write_transaction(db_path) as conn:
        if payload.reset_registrations:
            conn.execute("DELETE FROM registrations")

        conn.execute("DELETE FROM mics")
        conn.execute("DELETE FROM interferences")

        for position, (mic_id, mic) in enumerate(zip(mic_ids, payload.mics)):
            conn.execute(
                """
                INSERT INTO mics(
                    mic_id, position, name, main_frequency_text, candidates_json
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (
                    mic_id,
                    position,
                    mic.name,
                    str(int(mic.main_frequency)),
                    json.dumps(list(mic.backup_candidates)),
                ),
            )
            if mic.installed_backup is not None:
                now = utc_now()
                conn.execute(
                    """
                    INSERT INTO registrations(
                        mic_id, backup_frequency_text, created_at, updated_at
                    ) VALUES(?, ?, ?, ?)
                    ON CONFLICT(mic_id) DO UPDATE SET
                        backup_frequency_text=excluded.backup_frequency_text,
                        updated_at=excluded.updated_at
                    """,
                    (mic_id, str(int(mic.installed_backup)), now, now),
                )
            elif "installed_backup" in getattr(mic, "model_fields_set", set()):
                # An explicit JSON null means "clear this mic's inline
                # registration"; omitting the field means preserve it.
                conn.execute(
                    "DELETE FROM registrations WHERE mic_id=?", (mic_id,)
                )

        for position, (interference_id, interference) in enumerate(
            zip(interference_ids, payload.interferences)
        ):
            conn.execute(
                """
                INSERT INTO interferences(
                    interference_id, position, name, frequencies_json
                ) VALUES(?, ?, ?, ?)
                """,
                (
                    interference_id,
                    position,
                    interference.name,
                    json.dumps(list(interference.occupied_frequencies)),
                ),
            )

        return _publish_version(conn, "configure", payload.name)


def replace_interferences(db_path: str, payload: Any) -> dict[str, Any]:
    interference_ids = _assign_identifiers(
        payload.interferences, "i", "interference_id"
    )
    with write_transaction(db_path) as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM mics").fetchone()["n"]
        if existing == 0:
            raise ApiError(
                409,
                "no_configuration",
                "尚未录入麦和主频；请先调用 /api/configuration。",
            )
        conn.execute("DELETE FROM interferences")
        for position, (interference_id, interference) in enumerate(
            zip(interference_ids, payload.interferences)
        ):
            conn.execute(
                """
                INSERT INTO interferences(
                    interference_id, position, name, frequencies_json
                ) VALUES(?, ?, ?, ?)
                """,
                (
                    interference_id,
                    position,
                    interference.name,
                    json.dumps(list(interference.occupied_frequencies)),
                ),
            )
        return _publish_version(conn, "update_interferences")


def register_backups(db_path: str, payload: Any) -> dict[str, Any]:
    items = payload.items()
    with write_transaction(db_path) as conn:
        resolved: list[tuple[str, int]] = []
        for item in items:
            mic_id = _resolve_mic_id(conn, item)
            resolved.append((mic_id, int(item.backup_frequency)))

        now = utc_now()
        for mic_id, frequency in resolved:
            conn.execute(
                """
                INSERT INTO registrations(
                    mic_id, backup_frequency_text, created_at, updated_at
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(mic_id) DO UPDATE SET
                    backup_frequency_text=excluded.backup_frequency_text,
                    updated_at=excluded.updated_at
                """,
                (mic_id, str(frequency), now, now),
            )
        return _publish_version(conn, "register_backup")


def delete_registration(db_path: str, mic_reference: str) -> dict[str, Any]:
    with write_transaction(db_path) as conn:
        rows = conn.execute("SELECT mic_id FROM mics ORDER BY position").fetchall()
        ids = [row["mic_id"] for row in rows]

        class Ref:
            mic_id = mic_reference
            mic_index = None
            mic_number = None

        mic_id = _resolve_mic_id(conn, Ref())
        conn.execute("DELETE FROM registrations WHERE mic_id=?", (mic_id,))
        table = _publish_version(conn, "delete_registration")
        table["deleted_registration"] = mic_id
        return table


def get_current_table(db_path: str) -> Optional[dict[str, Any]]:
    conn = connect(db_path)
    try:
        version = _meta_get(conn, "current_version")
        if version is None:
            return None
        row = conn.execute(
            "SELECT * FROM versions WHERE version=?", (int(version),)
        ).fetchone()
        if row is None:
            return None
        configuration = json.loads(row["config_json"])
        result = json.loads(row["result_json"])
        return {
            "effective_version": row["version"],
            "effective_at": row["created_at"],
            "trigger": row["trigger_name"],
            "configuration": configuration,
            **result,
        }
    finally:
        conn.close()


def list_versions(db_path: str, limit: int = 20) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT version, created_at, trigger_name, setup_name, result_json
            FROM versions ORDER BY version DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        versions = []
        for row in rows:
            result = json.loads(row["result_json"])
            versions.append(
                {
                    "effective_version": row["version"],
                    "effective_at": row["created_at"],
                    "trigger": row["trigger_name"],
                    "setup_name": row["setup_name"],
                    "status": result.get("status"),
                    "feasible": result.get("feasible"),
                    "message": result.get("message"),
                }
            )
        return versions
    finally:
        conn.close()
