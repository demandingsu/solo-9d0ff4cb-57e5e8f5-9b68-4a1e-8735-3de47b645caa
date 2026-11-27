"""Pydantic request/response schemas and Swagger examples."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _as_int_list(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [int(value)]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            import json

            return json.loads(text)
        return [int(part.strip()) for part in text.split(",") if part.strip()]
    return value


class MicIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    mic_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "mic_id", "id", "microphone_id", "麦ID", "麦编号", "编号"
        ),
        description="麦的稳定 ID；省略时按位置生成 m1..m6。",
    )
    name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "name", "mic_name", "label", "名称", "麦名"
        ),
    )
    main_frequency: int = Field(
        validation_alias=AliasChoices(
            "main_frequency",
            "main_freq",
            "main",
            "frequency",
            "primary_frequency",
            "tx_frequency",
            "主频",
            "主频点",
        ),
        description="主频，所有频点使用同一单位的整数。",
    )
    backup_candidates: list[int] = Field(
        min_length=1,
        max_length=4,
        validation_alias=AliasChoices(
            "backup_candidates",
            "backup_frequencies",
            "backup_freqs",
            "candidates",
            "backups",
            "备频候选",
            "备频",
            "候选频点",
        ),
        description="1～4 个备频候选。",
    )
    installed_backup: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices(
            "installed_backup",
            "registered_backup",
            "written_backup",
            "prestored_backup",
            "已写备频",
            "登记备频",
        ),
        description="可选：内联登记已写入该麦的备频；后续重配强制沿用。",
    )

    @field_validator("mic_id", mode="before")
    @classmethod
    def stringify_id(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("backup_candidates", mode="before")
    @classmethod
    def wrap_candidates(cls, value: Any) -> Any:
        return _as_int_list(value)


class InterferenceIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    interference_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "interference_id", "id", "scenario_id", "干扰ID", "编号"
        ),
    )
    name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "name", "label", "scenario_name", "type", "名称", "干扰名称"
        ),
    )
    occupied_frequencies: list[int] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "occupied_frequencies",
            "frequencies",
            "blocked_frequencies",
            "occupied",
            "blocked",
            "freqs",
            "占用频点",
            "频点",
        ),
        description="该干扰情形占用的频点。",
    )

    @field_validator("interference_id", mode="before")
    @classmethod
    def stringify_id(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("occupied_frequencies", mode="before")
    @classmethod
    def wrap_frequencies(cls, value: Any) -> Any:
        return _as_int_list(value)


class ConfigurationIn(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        json_schema_extra={
            "example": {
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
                ],
                "interferences": [
                    {
                        "interference_id": "wifi-a",
                        "name": "Wi-Fi A",
                        "occupied_frequencies": [480, 490],
                    }
                ],
            }
        },
    )

    name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "name", "setup_name", "title", "名称", "场景名称"
        ),
    )
    reset_registrations: bool = Field(
        default=False,
        description="为 true 时先清空旧的已写备频登记，再保存本套设置。",
    )
    mics: list[MicIn] = Field(
        default_factory=list,
        max_length=6,
        validation_alias=AliasChoices(
            "mics",
            "microphones",
            "mic_list",
            "microphone_list",
            "麦",
            "麦克风",
        ),
    )
    interferences: list[InterferenceIn] = Field(
        default_factory=list,
        max_length=8,
        validation_alias=AliasChoices(
            "interferences",
            "interference_list",
            "scenarios",
            "jammers",
            "干扰",
            "干扰清单",
        ),
    )


class InterferenceUpdate(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        json_schema_extra={
            "example": {
                "interferences": [
                    {
                        "interference_id": "wifi-a",
                        "name": "Wi-Fi A",
                        "occupied_frequencies": [480, 490, 500],
                    },
                    {
                        "interference_id": "led",
                        "name": "LED 墙",
                        "occupied_frequencies": [520, 528],
                    },
                ]
            }
        },
    )

    interferences: list[InterferenceIn] = Field(
        max_length=8,
        validation_alias=AliasChoices(
            "interferences", "items", "scenarios", "interference_list"
        ),
    )


class RegistrationItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    mic_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "mic_id", "id", "microphone_id", "麦ID", "编号"
        ),
    )
    mic_index: Optional[int] = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("mic_index", "index", "数组下标"),
    )
    mic_number: Optional[int] = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices("mic_number", "number", "麦号"),
    )
    backup_frequency: int = Field(
        validation_alias=AliasChoices(
            "backup_frequency",
            "frequency",
            "installed_backup",
            "backup",
            "备频",
            "频点",
        )
    )

    @field_validator("mic_id", mode="before")
    @classmethod
    def stringify_id(cls, value: Any) -> Any:
        if value is None:
            return None
        return str(value).strip() or None


class RegistrationIn(BaseModel):
    """Single registration or batch wrapper accepted by one endpoint."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        json_schema_extra={
            "example": {"mic_id": "m1", "backup_frequency": 500},
            "examples": [
                {"mic_id": "m1", "backup_frequency": 500},
                {
                    "registrations": [
                        {"mic_id": "m1", "backup_frequency": 500},
                        {"mic_index": 1, "backup_frequency": 536},
                    ]
                },
            ],
        },
    )

    mic_id: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("mic_id", "id")
    )
    mic_index: Optional[int] = Field(default=None, ge=0)
    mic_number: Optional[int] = Field(default=None, ge=1)
    backup_frequency: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices(
            "backup_frequency", "frequency", "installed_backup", "backup"
        ),
    )
    registrations: Optional[list[RegistrationItem]] = None
    assignments: Optional[list[RegistrationItem]] = None

    def items(self) -> list[RegistrationItem]:
        batch = self.registrations or self.assignments
        if batch is not None:
            return batch
        if self.backup_frequency is None:
            raise ValueError(
                "backup_frequency is required for a single registration"
            )
        return [
            RegistrationItem(
                mic_id=self.mic_id,
                mic_index=self.mic_index,
                mic_number=self.mic_number,
                backup_frequency=self.backup_frequency,
            )
        ]


class PathRegistrationIn(BaseModel):
    backup_frequency: int = Field(
        validation_alias=AliasChoices(
            "backup_frequency", "frequency", "installed_backup", "backup"
        )
    )


# ---------------------------------------------------------------------------
# Rehearsal dropout review ("复盘")
# ---------------------------------------------------------------------------


class ReviewSegmentIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    start: int = Field(
        validation_alias=AliasChoices(
            "start",
            "start_ms",
            "begin",
            "from_time",
            "from",
            "lower",
            "start_time",
            "起点",
            "开始",
        ),
        description="发射区间起点（含），整数毫秒。",
    )
    end: int = Field(
        validation_alias=AliasChoices(
            "end",
            "end_ms",
            "stop",
            "to_time",
            "to",
            "upper",
            "end_time",
            "终点",
            "结束",
        ),
        description="发射区间终点（不含），整数毫秒。",
    )
    frequency: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices(
            "frequency", "freq", "tx_frequency", "f", "频点", "频率"
        ),
        description="该段实测发射频点；省略时使用麦级 frequency。",
    )


class ReviewEventIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    time: int = Field(
        validation_alias=AliasChoices(
            "time",
            "timestamp",
            "time_ms",
            "at",
            "t",
            "log_time",
            "moment",
            "时刻",
            "时间",
        ),
        description="接收机日志时刻，整数毫秒。",
    )
    status: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "status", "state", "result", "observed", "type", "状态"
        ),
        description="dropout/掉音 或 normal/正常。",
    )
    dropout: Optional[bool] = Field(
        default=None,
        validation_alias=AliasChoices(
            "dropout", "is_dropout", "dropped", "is_drop", "掉音"
        ),
    )
    label: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("label", "note", "name", "remark", "备注"),
    )

    @model_validator(mode="after")
    def _normalize_status(self) -> "ReviewEventIn":
        # Local import avoids a schemas -> review dependency at import time.
        from .review import normalize_status

        if self.status is not None:
            is_dropout = normalize_status(self.status)
        elif self.dropout is not None:
            is_dropout = bool(self.dropout)
        else:
            raise ValueError("每条记录必须给出 status（dropout/normal）或 dropout 布尔值。")
        self.dropout = is_dropout
        self.status = "dropout" if is_dropout else "normal"
        return self


class ReviewMicIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    mic_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "mic_id", "id", "microphone_id", "mic", "麦ID", "麦编号", "编号"
        ),
    )
    name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "name", "mic_name", "label", "microphone_name", "名称", "麦名"
        ),
    )
    frequency: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices(
            "frequency",
            "main_frequency",
            "freq",
            "tx_frequency",
            "default_frequency",
            "主频",
            "频点",
        ),
        description="麦级默认实测频点；段内未给 frequency 时使用。",
    )
    clock_offset_range: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices(
            "clock_offset_range",
            "offset_range",
            "time_offset_range",
            "clock_offsets",
            "offset",
            "offsets",
            "clock_offset",
            "drift_range",
            "time_offset",
            "时钟偏移范围",
            "偏移范围",
        ),
        description=(
            "含两端的整数毫秒范围 [lo, hi]（最多两支麦填写）；"
            "也接受 {min,max} 或固定整数偏移；省略按 0。"
        ),
        json_schema_extra={
            "anyOf": [
                {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
                {"type": "object"},
                {"type": "integer"},
                {"type": "null"},
            ]
        },
    )
    segments: list[ReviewSegmentIn] = Field(
        default_factory=list,
        max_length=8,
        validation_alias=AliasChoices(
            "segments",
            "transmissions",
            "intervals",
            "ranges",
            "windows",
            "slots",
            "发射区间",
            "区间",
            "段",
        ),
        description="最多 8 段实测发射区间 [start, end)。",
    )

    @field_validator("mic_id", mode="before")
    @classmethod
    def stringify_id(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("segments", mode="before")
    @classmethod
    def _wrap_segments(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, dict):
            # {"1000-2000": {...}} is unlikely; treat a single segment object
            # or a mapping of id -> segment uniformly.
            if "start" in value or "end" in value:
                return [value]
            return list(value.values())
        return value

    @model_validator(mode="after")
    def _fill_defaults_and_validate(self) -> "ReviewMicIn":
        if self.mic_id is None:
            raise ValueError("每支实测麦都必须给出 mic_id。")
        for segment in self.segments:
            if segment.frequency is None:
                if self.frequency is None:
                    raise ValueError(
                        "每个发射段必须给出 frequency，或在麦级给出默认 frequency。"
                    )
                segment.frequency = self.frequency

        ordered = sorted(self.segments, key=lambda s: (s.start, s.end))
        for prev, nxt in zip(ordered, ordered[1:]):
            if prev.start < nxt.end and nxt.start < prev.end:
                raise ValueError(
                    "同一支麦的发射区间必须互不重叠（含起点、不含终点）："
                    f"[{prev.start},{prev.end}) 与 [{nxt.start},{nxt.end})。"
                )
        return self


class ReviewRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        json_schema_extra={
            "example": {
                "effective_version": 1,
                "target_mic_id": "m1",
                "target_frequency": 480,
                "events": [
                    {"time": 1000, "status": "dropout"},
                ],
                "mics": [
                    {
                        "mic_id": "m2",
                        "clock_offset_range": [0, 50],
                        "segments": [
                            {"start": 1000, "end": 1001, "frequency": 480},
                            {"start": 1010, "end": 1021, "frequency": 520},
                        ],
                    },
                    {
                        "mic_id": "m3",
                        "segments": [
                            {"start": 1000, "end": 1001, "frequency": 500}
                        ],
                    },
                ],
            }
        },
    )

    effective_version: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices(
            "effective_version",
            "version",
            "version_id",
            "version_number",
            "effectiveVersion",
            "配频版本",
            "版本",
        ),
        description="选配频版本；省略时使用当前生效版本。",
    )
    target_mic_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "target_mic_id",
            "target_mic",
            "target",
            "mic_id",
            "target_id",
            "目标麦",
        ),
        description="目标麦 mic_id。",
    )
    target_frequency: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices(
            "target_frequency",
            "fixed_frequency",
            "target_freq",
            "frequency",
            "tx_frequency",
            "固定频点",
            "目标频点",
        ),
        description="目标麦全程使用的固定频点；省略时取该版本中目标麦主频。",
    )
    events: list[ReviewEventIn] = Field(
        default_factory=list,
        max_length=8,
        validation_alias=AliasChoices(
            "events",
            "observations",
            "logs",
            "records",
            "samples",
            "timeline",
            "reports",
            "event_log",
            "接收机记录",
            "记录",
            "时刻",
        ),
    )
    mics: list[ReviewMicIn] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "mics",
            "other_mics",
            "transmitters",
            "microphones",
            "measurements",
            "mic_measurements",
            "实测麦",
            "其他麦",
        ),
    )

    @field_validator("target_mic_id", mode="before")
    @classmethod
    def _stringify_target(cls, value: Any) -> Any:
        if value is None:
            return None
        return str(value).strip() or None

    @field_validator("events", mode="before")
    @classmethod
    def _dict_events_to_list(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return [
                {"time": time, "status": status}
                for time, status in value.items()
            ]
        return value

    @field_validator("mics", mode="before")
    @classmethod
    def _dict_mics_to_list(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return [
                dict(payload, mic_id=key) if isinstance(payload, dict) else payload
                for key, payload in value.items()
            ]
        return value

    @model_validator(mode="before")
    @classmethod
    def _unwrap_nested_target(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("target"), dict):
            nested = data["target"]
            data = dict(data)
            if data.get("target_mic_id") is None:
                mic = nested.get("mic_id") or nested.get("id")
                if mic is not None:
                    data["target_mic_id"] = mic
            if data.get("target_frequency") is None:
                freq = (
                    nested.get("fixed_frequency")
                    or nested.get("target_frequency")
                    or nested.get("frequency")
                )
                if freq is not None:
                    data["target_frequency"] = freq
        return data


REVIEW_EXAMPLE_EXPLAINABLE: dict[str, Any] = {
    "effective_version": 1,
    "target_mic_id": "m1",
    "target_frequency": 480,
    "events": [
        {"time": 1000, "status": "dropout", "label": "单次掉音"}
    ],
    "mics": [
        {
            "mic_id": "m2",
            "name": "独唱",
            "clock_offset_range": [0, 50],
            "segments": [
                {"start": 1000, "end": 1001, "frequency": 480},
                {"start": 1010, "end": 1021, "frequency": 520},
            ],
        },
        {
            "mic_id": "m3",
            "name": "嘉宾",
            "segments": [{"start": 1000, "end": 1001, "frequency": 500}],
        },
    ],
}

REVIEW_EXAMPLE_NO_SOLUTION: dict[str, Any] = {
    "effective_version": 1,
    "target_mic_id": "m1",
    "target_frequency": 480,
    "events": [
        {"time": 100, "status": "dropout"},
        {"time": 200, "status": "normal"},
    ],
    "mics": [
        {
            "mic_id": "m2",
            "name": "独唱",
            "clock_offset_range": [0, 50],
            "segments": [
                {"start": 100, "end": 101, "frequency": 480},
                {"start": 200, "end": 201, "frequency": 480},
            ],
        }
    ],
}

REVIEW_EXAMPLE_FULL_SESSION: dict[str, Any] = {
    "effective_version": 1,
    "target_mic_id": "m1",
    "target_frequency": 600,
    "events": [
        {"time": 0, "status": "normal"},
        {"time": 1000, "status": "dropout"},
        {"time": 2000, "status": "normal"},
        {"time": 3000, "status": "dropout"},
    ],
    "mics": [
        {
            "mic_id": "m2",
            "clock_offset_range": [-20, 20],
            "segments": [
                {"start": 990, "end": 1031, "frequency": 600},
                {"start": 2980, "end": 3021, "frequency": 640},
            ],
        },
        {
            "mic_id": "m3",
            "clock_offset_range": [-20, 20],
            "segments": [
                {"start": 2980, "end": 3021, "frequency": 680},
            ],
        },
    ],
}
