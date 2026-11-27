"""Pydantic request/response schemas and Swagger examples."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


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
