"""FastAPI application for wireless-microphone frequency allocation."""

from __future__ import annotations

import os
from typing import Any, Union

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import storage
from .schemas import (
    ConfigurationIn,
    InterferenceIn,
    InterferenceUpdate,
    PathRegistrationIn,
    RegistrationIn,
    RegistrationItem,
)

InterferencePayload = Union[InterferenceUpdate, list[InterferenceIn]]
RegistrationPayload = Union[RegistrationIn, list[RegistrationItem]]
ConfigurationPayload = Union[ConfigurationIn, list[Any]]

DEFAULT_EXAMPLE = {
    "name": "晚会现场配频",
    "mics": [
        {
            "mic_id": "m1",
            "name": "主持",
            "main_frequency": 480,
            "backup_candidates": [490, 500, 510],
        },
        {
            "mic_id": "m2",
            "name": "独唱",
            "main_frequency": 520,
            "backup_candidates": [528, 536],
        },
        {
            "mic_id": "m3",
            "name": "嘉宾",
            "main_frequency": 561,
            "backup_candidates": [570, 580, 590, 600],
        },
    ],
    "interferences": [
        {
            "interference_id": "wifi-a",
            "name": "Wi-Fi 频段 A",
            "occupied_frequencies": [480, 490],
        },
        {
            "interference_id": "led-wall",
            "name": "LED 控制干扰",
            "occupied_frequencies": [520, 528],
        },
    ],
}


def create_app(database_path: str | None = None) -> FastAPI:
    db_path = database_path or os.getenv("DATABASE_PATH", "frequency.db")
    storage.init_db(db_path)

    app = FastAPI(
        title="无线麦配频 API",
        version="1.0.0",
        description=(
            "录入最多 6 支麦、每支 1～4 个备频候选和最多 8 种干扰；"
            "为每支麦选定一档必须沿用的备频，并返回各干扰情形的切换表。\n\n"
            "工作流：\n"
            "1. `POST /api/configuration` 试配并保存整套设置；\n"
            "2. `POST /api/registrations` 登记已写入麦里的备频；\n"
            "3. `PUT /api/interferences` 修改干扰清单；\n"
            "4. `GET /api/table` 查询同一生效版本的设置与切换表。"
        ),
    )
    app.state.database_path = db_path

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(storage.ApiError)
    async def api_error_handler(_: Request, exc: storage.ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {
                    "error": "validation_error",
                    "message": "请求结构或数量限制不符合要求。",
                    "details": exc.errors(),
                }
            ),
        )

    @app.get("/", tags=["meta"])
    def root() -> dict[str, Any]:
        return {
            "service": "wireless-microphone-frequency-api",
            "docs": "/docs",
            "health": "/health",
            "table": "/api/table",
            "examples": DEFAULT_EXAMPLE,
        }

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    def db(request: Request) -> str:
        return request.app.state.database_path

    @app.post(
        "/api/configuration",
        tags=["配频设置"],
        summary="录入/替换整套麦、备频候选与干扰并立即试配",
        operation_id="post_configuration",
    )
    @app.put(
        "/api/configuration",
        tags=["配频设置"],
        summary="录入/替换整套麦、备频候选与干扰并立即试配",
        operation_id="put_configuration",
    )
    @app.api_route(
        "/api/config",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/setup",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/settings",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/allocate",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/frequency-plan",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/frequency-assignment",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/assignment",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/assignments",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/mics",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/microphones",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    @app.api_route(
        "/configure",
        methods=["POST", "PUT"],
        tags=["配频设置"],
        include_in_schema=False,
    )
    def save_configuration(
        payload: ConfigurationPayload, request: Request
    ) -> dict[str, Any]:
        if isinstance(payload, list):
            configuration = ConfigurationIn(mics=payload)
        else:
            configuration = payload
        return storage.save_configuration(db(request), configuration)

    @app.get(
        "/api/configuration/current",
        tags=["配频设置"],
        summary="读取当前生效的整套设置与配频结果",
    )
    def current_configuration(request: Request) -> dict[str, Any]:
        table = storage.get_current_table(db(request))
        if table is None:
            return {
                "status": "not_configured",
                "message": "尚未录入设置。",
                "configuration": None,
            }
        return table

    @app.get(
        "/api/mics",
        tags=["配频设置"],
        summary="读取当前麦、主频、候选和登记备频",
    )
    @app.get("/api/microphones", tags=["配频设置"], include_in_schema=False)
    def get_mics(request: Request) -> dict[str, Any]:
        table = storage.get_current_table(db(request))
        if table is None:
            return {"mics": [], "message": "尚未录入设置。"}
        return {"mics": table["configuration"]["mics"]}

    def _as_interference_update(
        payload: InterferencePayload,
    ) -> InterferenceUpdate:
        if isinstance(payload, list):
            return InterferenceUpdate(interferences=payload)
        return payload

    @app.get(
        "/api/interferences",
        tags=["干扰清单"],
        summary="读取当前干扰清单",
    )
    def get_interferences(request: Request) -> dict[str, Any]:
        table = storage.get_current_table(db(request))
        if table is None:
            return {"interferences": [], "message": "尚未录入设置。"}
        return {
            "interferences": table["configuration"]["interferences"],
            "effective_version": table["effective_version"],
        }

    @app.put(
        "/api/interferences",
        tags=["干扰清单"],
        summary="整体修改最多 8 种干扰清单并重新配频",
        operation_id="put_interferences",
    )
    @app.post(
        "/api/interferences",
        tags=["干扰清单"],
        summary="整体修改最多 8 种干扰清单并重新配频",
        operation_id="post_interferences",
    )
    @app.patch(
        "/api/interferences",
        tags=["干扰清单"],
        summary="整体修改最多 8 种干扰清单并重新配频",
        operation_id="patch_interferences",
    )
    @app.api_route(
        "/interferences",
        methods=["PUT", "POST", "PATCH"],
        tags=["干扰清单"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/interference",
        methods=["PUT", "POST", "PATCH"],
        tags=["干扰清单"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/interference-list",
        methods=["PUT", "POST", "PATCH"],
        tags=["干扰清单"],
        include_in_schema=False,
    )
    def replace_interferences(
        payload: InterferencePayload, request: Request
    ) -> dict[str, Any]:
        update = _as_interference_update(payload)
        return storage.replace_interferences(db(request), update)

    def _as_registration(payload: RegistrationPayload) -> RegistrationIn:
        if isinstance(payload, list):
            return RegistrationIn(registrations=payload)
        return payload

    @app.post(
        "/api/registrations",
        tags=["备频登记"],
        summary="登记一支或多支麦已经写入的备频；重配强制沿用",
        operation_id="post_registrations",
    )
    @app.put(
        "/api/registrations",
        tags=["备频登记"],
        summary="登记一支或多支麦已经写入的备频；重配强制沿用",
        operation_id="put_registrations",
    )
    @app.api_route(
        "/api/backups/register",
        methods=["POST", "PUT"],
        tags=["备频登记"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/register",
        methods=["POST", "PUT"],
        tags=["备频登记"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/backup",
        methods=["POST", "PUT"],
        tags=["备频登记"],
        include_in_schema=False,
    )
    @app.api_route(
        "/api/backups",
        methods=["POST", "PUT"],
        tags=["备频登记"],
        include_in_schema=False,
    )
    def register_backup(
        payload: RegistrationPayload, request: Request
    ) -> dict[str, Any]:
        registration = _as_registration(payload)
        try:
            # Validate early so an empty body produces a clean 422.
            registration.items()
        except ValueError as exc:
            raise storage.ApiError(422, "invalid_registration", str(exc)) from exc
        return storage.register_backups(db(request), registration)

    @app.put(
        "/api/mics/{mic_id}/installed-backup",
        tags=["备频登记"],
        summary="按 mic_id 登记该麦已写入的备频",
        operation_id="put_mic_installed_backup",
    )
    @app.post(
        "/api/mics/{mic_id}/installed-backup",
        tags=["备频登记"],
        summary="按 mic_id 登记该麦已写入的备频",
        operation_id="post_mic_installed_backup",
    )
    def register_path_backup(
        mic_id: str, body: PathRegistrationIn, request: Request
    ) -> dict[str, Any]:
        registration = RegistrationIn(
            mic_id=mic_id, backup_frequency=body.backup_frequency
        )
        return storage.register_backups(db(request), registration)

    @app.api_route(
        "/api/registrations/{mic_id}",
        methods=["DELETE"],
        tags=["备频登记"],
        summary="删除一支麦的备频登记",
    )
    def delete_registration(mic_id: str, request: Request) -> dict[str, Any]:
        return storage.delete_registration(db(request), mic_id)

    @app.get(
        "/api/table",
        tags=["查表"],
        summary="查询同一生效版本的设置、备频和各干扰切换表",
    )
    @app.get("/table", tags=["查表"], include_in_schema=False)
    @app.get("/api/allocation-table", tags=["查表"], include_in_schema=False)
    @app.get("/api/switch-table", tags=["查表"], include_in_schema=False)
    @app.get("/api/switching-table", tags=["查表"], include_in_schema=False)
    @app.get("/api/frequency-table", tags=["查表"], include_in_schema=False)
    @app.get("/api/assignment-table", tags=["查表"], include_in_schema=False)
    @app.get("/api/assignments", tags=["查表"], include_in_schema=False)
    @app.get("/api/assignment", tags=["查表"], include_in_schema=False)
    @app.get("/api/frequency-assignment", tags=["查表"], include_in_schema=False)
    @app.get("/api/plan", tags=["查表"], include_in_schema=False)
    @app.get("/api/frequency-plan", tags=["查表"], include_in_schema=False)
    @app.get("/api/query", tags=["查表"], include_in_schema=False)
    def get_table(request: Request) -> dict[str, Any]:
        table = storage.get_current_table(db(request))
        if table is None:
            return {
                "status": "not_configured",
                "feasible": None,
                "effective_version": None,
                "configuration": None,
                "selected_backups": None,
                "switch_table": None,
                "no_solution": None,
                "message": "尚未录入设置；请先 POST /api/configuration。",
            }
        return table

    @app.get(
        "/api/versions",
        tags=["查表"],
        summary="查看历次生效版本的状态",
    )
    def versions(request: Request, limit: int = 20) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 100))
        return {
            "versions": storage.list_versions(db(request), limit=safe_limit)
        }

    return app


app = create_app()
