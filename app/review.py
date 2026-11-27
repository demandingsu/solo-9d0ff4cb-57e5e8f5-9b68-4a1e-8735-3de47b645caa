"""Rehearsal dropout review / clock-offset forensics.

This module is intentionally independent from FastAPI and SQLite so the timing
logic can be unit tested and differential-checked against exhaustive offset
enumeration.

Time convention (all values are integer milliseconds):

* A receiver log event happens at time ``t``.
* A microphone clock offset ``d`` is **added to the log time**, so the
  microphone-frame instant to test is ``s = t + d``.
* A measured transmission segment ``[start, end)`` contains that instant iff
  ``start <= s < end`` (start included, end excluded).
* A microphone without an offset range always uses offset ``0``.
* At most two microphones may declare an inclusive integer offset range
  ``[lo, hi]``; one microphone keeps one offset for the whole session.

Algorithm overview
------------------
For an event ``t`` and a segment ``[start, end)`` of an offset-variable mic
``d in [lo, hi]``, the segment is active exactly when
``start - t <= d < end - t``.  Every truth value therefore only changes at
the integer cut points ``start - t`` and ``end - t``.  Collecting all such
cuts (plus ``lo`` and ``hi + 1``) partitions each offset dimension into a
small number of atomic cells on which every activation, frequency and
conflict predicate is constant.  With at most two variable microphones the
whole problem becomes a tiny grid whose size depends on event/segment counts,
never on the number of milliseconds spanned by ``[lo, hi]``.
"""

from __future__ import annotations

from itertools import product
from typing import Any, Mapping, Sequence

MAX_EVENTS = 8
MAX_SEGMENTS_PER_MIC = 8
MAX_OFFSET_MICS = 2

DROPOUT_WORDS = {
    "dropout",
    "drop",
    "dropped",
    "drop-out",
    "outage",
    "cut",
    "cutout",
    "cut-out",
    "mute",
    "muted",
    "lost",
    "loss",
    "fail",
    "failed",
    "failure",
    "bad",
    "offline",
    "rf-loss",
    "掉音",
    "断音",
    "掉线",
    "中断",
    "丢音",
    "断续",
    "异常",
}
NORMAL_WORDS = {
    "normal",
    "ok",
    "okay",
    "good",
    "fine",
    "clear",
    "healthy",
    "connected",
    "正常",
    "良好",
}


class ReviewInputError(ValueError):
    """Semantic (non-schema) problem with a review request."""

    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details


# ---------------------------------------------------------------------------
# Normalization helpers (used by the HTTP layer and usable directly)
# ---------------------------------------------------------------------------


def _pick(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def normalize_status(value: Any) -> bool:
    """Return ``True`` for dropout, ``False`` for normal; raise on garbage."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if int(value) == 1:
            return True
        if int(value) == 0:
            return False
    text = str(value).strip().lower()
    if text in DROPOUT_WORDS:
        return True
    if text in NORMAL_WORDS:
        return False
    # Chinese substring tolerance ("出现掉音" etc.).
    if "掉音" in text or "断音" in text or "dropout" in text:
        return True
    if "正常" in text:
        return False
    raise ReviewInputError(
        f"无法识别的记录状态：{value!r}；请使用 dropout/掉音 或 normal/正常。",
        {"value": value, "allowed": ["dropout", "normal"]},
    )


def normalize_offset_range(value: Any) -> tuple[int, int] | None:
    spec = normalize_offset_spec(value)
    return None if spec is None else (spec[0], spec[1])


def normalize_offset_spec(value: Any) -> tuple[int, int, bool] | None:
    """Normalize an offset input.

    Returns ``(lo, hi, is_range_form)`` where ``is_range_form`` marks an
    explicit two-ended range (``[lo, hi]`` / ``{min,max}``); a bare scalar is
    a known constant offset and does not consume one of the two range slots.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        raise ReviewInputError("时钟偏移范围不能是布尔值。", {"value": value})
    if isinstance(value, (int, float)):
        bound = int(value)
        return bound, bound, False
    if isinstance(value, str):
        text = value.strip()
        for separator in ("..", "~", ",", " "):
            if separator in text:
                parts = [p for p in text.replace("..", ",").replace("~", ",").split(",") if p.strip()]
                if len(parts) == 2:
                    lo, hi = int(parts[0]), int(parts[1])
                    if lo > hi:
                        raise ReviewInputError(
                            "时钟偏移范围下限不能大于上限。",
                            {"offset_min": lo, "offset_max": hi},
                        )
                    return lo, hi, True
        raise ReviewInputError(
            "无法解析的时钟偏移范围字符串，请使用 \"lo,hi\"。", {"value": value}
        )
    if isinstance(value, Mapping):
        lo = _pick(value, "min", "lo", "lower", "start", "offset_min", "from", "下限")
        hi = _pick(value, "max", "hi", "upper", "end", "offset_max", "to", "上限")
        if lo is None or hi is None:
            raise ReviewInputError(
                "时钟偏移范围对象必须同时给出 min/max（或 lower/upper）。",
                {"value": value},
            )
        lo, hi = int(lo), int(hi)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) != 2:
            raise ReviewInputError(
                "时钟偏移范围必须是含两端的两个整数 [offset_min, offset_max]。",
                {"value": list(value)},
            )
        lo, hi = int(value[0]), int(value[1])
    else:
        raise ReviewInputError(
            "时钟偏移范围必须是 [offset_min, offset_max] 或 {min,max}。",
            {"value": value},
        )
    if lo > hi:
        raise ReviewInputError(
            "时钟偏移范围下限不能大于上限。", {"offset_min": lo, "offset_max": hi}
        )
    return lo, hi, True


def _normalize_segment(raw: Mapping[str, Any], mic_frequency: int | None) -> dict[str, Any]:
    start = _pick(raw, "start", "start_ms", "begin", "from", "lower", "起点", "开始")
    end = _pick(raw, "end", "end_ms", "stop", "to", "upper", "终点", "结束")
    frequency = _pick(
        raw, "frequency", "freq", "tx_frequency", "f", "频点", "频率"
    )
    if start is None or end is None:
        raise ReviewInputError(
            "每个发射区间必须给出 start（含）和 end（不含）。", {"segment": dict(raw)}
        )
    start, end = int(start), int(end)
    if start >= end:
        raise ReviewInputError(
            "发射区间必须满足 start < end，且为 [start, end)。",
            {"start": start, "end": end},
        )
    if frequency is None:
        frequency = mic_frequency
    if frequency is None:
        raise ReviewInputError(
            "每个发射段必须给出实测频点 frequency（或在麦级别给出默认频点）。",
            {"segment": dict(raw)},
        )
    return {"start": start, "end": end, "frequency": int(frequency)}


def normalize_mic(raw: Mapping[str, Any]) -> dict[str, Any]:
    mic_id = _pick(raw, "mic_id", "id", "microphone_id", "mic", "麦ID", "麦编号")
    if mic_id is None or str(mic_id).strip() == "":
        raise ReviewInputError("每条其他麦的实测数据都必须给出 mic_id。", {"mic": dict(raw)})
    mic_id = str(mic_id).strip()
    name = _pick(raw, "name", "mic_name", "label", "名称", "麦名")
    mic_frequency = _pick(
        raw, "frequency", "main_frequency", "freq", "tx_frequency", "主频", "频点"
    )
    mic_frequency = int(mic_frequency) if mic_frequency is not None else None

    raw_segments = _pick(
        raw,
        "segments",
        "transmissions",
        "intervals",
        "windows",
        "slots",
        "发射区间",
        "区间",
        "段",
    )
    if raw_segments is None:
        raw_segments = []
    if isinstance(raw_segments, Mapping):
        raw_segments = [raw_segments]
    segments = [
        _normalize_segment(seg, mic_frequency) for seg in list(raw_segments)
    ]
    if len(segments) > MAX_SEGMENTS_PER_MIC:
        raise ReviewInputError(
            f"每支麦最多 {MAX_SEGMENTS_PER_MIC} 段发射区间。",
            {"mic_id": mic_id, "segments": len(segments)},
        )

    offset_spec = normalize_offset_spec(
        _pick(
            raw,
            "clock_offset_range",
            "offset_range",
            "time_offset_range",
            "clock_offsets",
            "offset",
            "offsets",
            "clock_offset",
            "drift_range",
            "时钟偏移范围",
            "偏移范围",
        )
    )
    offset_range = None if offset_spec is None else (offset_spec[0], offset_spec[1])
    offset_is_range_form = bool(offset_spec and offset_spec[2])

    # Validate half-open non-overlap: [a0,a1) and [b0,b1) overlap iff
    # a0 < b1 and b0 < a1.  Touching endpoints do not overlap.
    ordered = sorted(segments, key=lambda s: (s["start"], s["end"]))
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev["start"] < nxt["end"] and nxt["start"] < prev["end"]:
            raise ReviewInputError(
                "同一支麦的发射区间必须互不重叠（含起点、不含终点）。",
                {
                    "mic_id": mic_id,
                    "first": [prev["start"], prev["end"]],
                    "second": [nxt["start"], nxt["end"]],
                },
            )

    return {
        "mic_id": mic_id,
        "name": str(name) if name is not None else None,
        "segments": ordered,
        "offset_range": offset_range,
        "offset_range_form": offset_is_range_form,
    }


def normalize_event(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ReviewInputError(
            "每条接收机记录必须是含 time 和 status 的对象。", {"index": index}
        )
    time = _pick(raw, "time", "timestamp", "time_ms", "at", "t", "log_time", "时刻", "时间")
    if time is None:
        raise ReviewInputError("每条记录必须给出整数毫秒时刻 time。", {"index": index})
    status_value = _pick(
        raw, "status", "state", "result", "type", "observed", "状态"
    )
    dropout_flag = _pick(raw, "dropout", "is_dropout", "dropped", "掉音")
    if status_value is None and dropout_flag is not None:
        is_dropout = bool(dropout_flag)
    elif status_value is not None:
        is_dropout = normalize_status(status_value)
    else:
        raise ReviewInputError(
            "每条记录必须给出 status（dropout/normal）或 dropout 布尔值。",
            {"index": index},
        )
    return {
        "time": int(time),
        "is_dropout": is_dropout,
        "status": "dropout" if is_dropout else "normal",
        "label": _pick(raw, "label", "note", "name", "备注"),
    }


def normalize_review(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a raw review payload into the canonical solver structure."""

    if not isinstance(payload, Mapping):
        raise ReviewInputError("复盘请求必须是 JSON 对象。")

    # Tolerate a nested target object: {"target": {"mic_id": "m1", ...}}.
    nested_target = _pick(payload, "target")
    if isinstance(nested_target, Mapping):
        payload = dict(payload)
        if payload.get("target_mic_id") is None:
            payload["target_mic_id"] = _pick(nested_target, "mic_id", "id")
        nested_freq = _pick(
            nested_target, "fixed_frequency", "target_frequency", "frequency"
        )
        if payload.get("target_frequency") is None and nested_freq is not None:
            payload["target_frequency"] = nested_freq

    target_id = _pick(
        payload,
        "target_mic_id",
        "target_mic",
        "target",
        "mic_id",
        "target_id",
        "目标麦",
    )
    if target_id is not None:
        target_id = str(target_id).strip() or None

    target_frequency = _pick(
        payload,
        "target_frequency",
        "fixed_frequency",
        "target_freq",
        "frequency",
        "tx_frequency",
        "固定频点",
        "目标频点",
    )
    if target_frequency is not None:
        target_frequency = int(target_frequency)

    raw_events = _pick(
        payload,
        "events",
        "observations",
        "logs",
        "records",
        "samples",
        "timeline",
        "reports",
        "event_log",
        "记录",
        "时刻",
    )
    if raw_events is None:
        raw_events = []
    if isinstance(raw_events, Mapping):
        raw_events = [{"time": t, "status": s} for t, s in raw_events.items()]
    raw_events = list(raw_events)
    if len(raw_events) > MAX_EVENTS:
        raise ReviewInputError(
            f"接收机记录最多 {MAX_EVENTS} 个时刻。", {"events": len(raw_events)}
        )
    events = [normalize_event(item, i) for i, item in enumerate(raw_events)]

    raw_mics = _pick(
        payload,
        "mics",
        "other_mics",
        "transmitters",
        "microphones",
        "measurements",
        "mic_measurements",
        "其他麦",
    )
    if raw_mics is None:
        raw_mics = []
    if isinstance(raw_mics, Mapping):
        raw_mics = [dict(value, mic_id=key) if isinstance(value, Mapping) else value
                    for key, value in raw_mics.items()]
    raw_mics = list(raw_mics)
    mics = [normalize_mic(item) for item in raw_mics]

    ids = [m["mic_id"] for m in mics]
    if len(set(ids)) != len(ids):
        raise ReviewInputError("实测数据中存在重复的 mic_id。", {"mic_ids": ids})

    # The cap is on explicit two-ended ranges; bare scalar offsets are known
    # constants and simply become one-cell dimensions.
    offset_mics = [
        m["mic_id"] for m in mics if m["offset_range_form"]
    ]
    if len(offset_mics) > MAX_OFFSET_MICS:
        raise ReviewInputError(
            f"最多 {MAX_OFFSET_MICS} 支麦可以填写含两端的时钟偏移范围。",
            {"offset_mics": offset_mics},
        )

    return {
        "target_mic_id": target_id,
        "target_frequency": target_frequency,
        "events": events,
        "mics": mics,
    }


# ---------------------------------------------------------------------------
# Core solver
# ---------------------------------------------------------------------------


def _active_segment(segments: Sequence[Mapping[str, Any]], shifted_time: int):
    for segment in segments:
        if segment["start"] <= shifted_time < segment["end"]:
            return segment
    return None


def _cause_signatures(
    target_frequency: int,
    active: Mapping[int, Mapping[str, Any]],
) -> tuple[list[int], list[tuple[int, int]], dict[tuple[int, int], tuple[int, int]]]:
    """Return co-channel mic indices and ordered intermod ``(a, b)`` indices.

    Also returns the first frequency realization of each pair.
    """

    cochannel: list[int] = []
    for mic_index, segment in active.items():
        if int(segment["frequency"]) == target_frequency:
            cochannel.append(mic_index)

    pairs: list[tuple[int, int]] = []
    pair_freqs: dict[tuple[int, int], tuple[int, int]] = {}
    items = list(active.items())
    for i, (a_index, a_segment) in enumerate(items):
        for j, (b_index, b_segment) in enumerate(items):
            if i == j:
                continue
            a_freq = int(a_segment["frequency"])
            b_freq = int(b_segment["frequency"])
            if 2 * a_freq - b_freq == target_frequency:
                key = (a_index, b_index)
                pairs.append(key)
                pair_freqs[key] = (a_freq, b_freq)
    return cochannel, pairs, pair_freqs


def review_incident(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Solve one rehearsal-dropout review.

    Accepts either the canonical normalized structure or a raw JSON-like
    mapping (aliases are normalized leniently).  Returns a JSON-friendly
    result dictionary.
    """

    # Normalization is idempotent for the canonical structure produced by the
    # Pydantic schemas and also understands raw aliases for direct callers.
    req = normalize_review(payload)

    target_id = req["target_mic_id"]
    target_frequency = req["target_frequency"]
    if target_frequency is None:
        raise ReviewInputError(
            "必须给出目标麦全程使用的固定频点 target_frequency。"
        )

    events = req["events"]
    # The target is the victim; never count its own measured rows as sources.
    mics = [m for m in req["mics"] if m["mic_id"] != target_id]

    # Only explicit two-ended ranges create search dimensions (at most two).
    # A bare scalar offset is a known constant and stays zero-dimensional.
    variable_positions = [
        i for i, mic in enumerate(mics) if mic.get("offset_range_form")
    ]
    if len(variable_positions) > MAX_OFFSET_MICS:  # defensive double-check
        raise ReviewInputError(
            f"最多 {MAX_OFFSET_MICS} 支麦可以填写含两端的时钟偏移范围。",
            {"offset_mics": [mics[i]["mic_id"] for i in variable_positions]},
        )

    dim_bounds: list[tuple[int, int]] = [
        mics[i]["offset_range"] for i in variable_positions  # type: ignore[misc]
    ]
    mic_dimension: dict[int, int] = {
        mic_index: dim for dim, mic_index in enumerate(variable_positions)
    }
    fixed_offsets: dict[int, int] = {}
    for mic_index, mic in enumerate(mics):
        if mic_index in mic_dimension:
            continue
        offset_range = mic.get("offset_range")
        fixed_offsets[mic_index] = int(offset_range[0]) if offset_range else 0

    # -- Build compressed atomic cells per offset dimension ------------------
    cut_sets: list[set[int]] = [{lo, hi + 1} for lo, hi in dim_bounds]
    for event in events:
        t = event["time"]
        for mic_index, mic in enumerate(mics):
            dim = mic_dimension.get(mic_index)
            if dim is None:
                continue
            cuts = cut_sets[dim]
            for segment in mic["segments"]:
                # start <= t + d < end  <=>  start-t <= d < end-t
                cuts.add(int(segment["start"]) - t)
                cuts.add(int(segment["end"]) - t)

    dimension_cells: list[list[int]] = []
    cut_counts: list[int] = []
    for dim, (lo, hi) in enumerate(dim_bounds):
        upper_exclusive = hi + 1
        ordered_cuts = sorted(cut_sets[dim])
        cut_counts.append(len(ordered_cuts))
        cells: list[int] = []
        for left, right in zip(ordered_cuts, ordered_cuts[1:]):
            if left >= lo and right <= upper_exclusive and left < right:
                # Every integer d in [left, right) is equivalent; ``left``
                # itself is always a valid representative.
                cells.append(left)
        dimension_cells.append(cells)

    if len(dimension_cells) == 0:
        index_combos: list[tuple[int, ...]] = [()]
    elif len(dimension_cells) == 1:
        index_combos = [(i,) for i in range(len(dimension_cells[0]))]
    else:
        index_combos = list(
            product(
                range(len(dimension_cells[0])),
                range(len(dimension_cells[1])),
            )
        )

    # -- Precompute active segment per event/mic/cell -----------------------
    # active_table[e][mic_index] is either a segment (fixed mic) or a list
    # aligned with the dimension's cells (variable mic).
    active_table: list[dict[int, Any]] = []
    for event in events:
        t = event["time"]
        per_mic: dict[int, Any] = {}
        for mic_index, mic in enumerate(mics):
            dim = mic_dimension.get(mic_index)
            if dim is None:
                per_mic[mic_index] = _active_segment(
                    mic["segments"], t + fixed_offsets[mic_index]
                )
            else:
                per_mic[mic_index] = [
                    _active_segment(mic["segments"], t + representative)
                    for representative in dimension_cells[dim]
                ]
        active_table.append(per_mic)

    # Candidate identities accumulated over globally feasible cells.
    feasible_cochannel: list[set[int]] = [set() for _ in events]
    feasible_pairs: list[set[tuple[int, int]]] = [set() for _ in events]
    pair_realizations: list[dict[tuple[int, int], set[tuple[int, int]]]] = [
        {} for _ in events
    ]

    # Domain-wide possibilities (diagnostic; meaningful when no solution).
    domain_cochannel: list[set[int]] = [set() for _ in events]
    domain_pairs: list[set[tuple[int, int]]] = [set() for _ in events]
    domain_pair_freqs: list[dict[tuple[int, int], set[tuple[int, int]]]] = [
        {} for _ in events
    ]

    feasible_cells = 0
    first_witness: tuple[int, ...] | None = None
    witness_event_details: list[dict[str, Any]] | None = None

    for combo in index_combos:
        offsets: dict[int, int] = {}
        for mic_index, dim in mic_dimension.items():
            offsets[mic_index] = dimension_cells[dim][combo[dim]]

        event_details: list[dict[str, Any]] = []
        combo_ok = True
        combo_causes: list[tuple[list[int], list[tuple[int, int]], dict]] = []

        for e, event in enumerate(events):
            active: dict[int, Mapping[str, Any]] = {}
            active_descriptions: list[dict[str, Any]] = []
            for mic_index, mic in enumerate(mics):
                dim = mic_dimension.get(mic_index)
                if dim is None:
                    segment = active_table[e][mic_index]
                    offset = fixed_offsets[mic_index]
                else:
                    segment = active_table[e][mic_index][combo[dim]]
                    offset = offsets[mic_index]
                if segment is not None:
                    active[mic_index] = segment
                    active_descriptions.append(
                        {
                            "mic_id": mic["mic_id"],
                            "frequency": int(segment["frequency"]),
                            "segment": [segment["start"], segment["end"]],
                            "applied_offset": offset,
                        }
                    )

            co_idx, pair_idx, pair_freqs = _cause_signatures(
                target_frequency, active
            )
            conflict = bool(co_idx or pair_idx)
            if event["is_dropout"]:
                satisfied = conflict
            else:
                satisfied = not conflict
            if not satisfied:
                combo_ok = False

            combo_causes.append((co_idx, pair_idx, pair_freqs))
            event_details.append(
                {
                    "active": active_descriptions,
                    "cochannel_indexes": co_idx,
                    "pair_indexes": pair_idx,
                    "pair_freqs": pair_freqs,
                    "conflict": conflict,
                    "satisfied": satisfied,
                }
            )

            domain_cochannel[e].update(co_idx)
            domain_pairs[e].update(pair_idx)
            freq_bucket = domain_pair_freqs[e]
            for key, freqs in pair_freqs.items():
                freq_bucket.setdefault(key, set()).add(freqs)

        if not combo_ok:
            continue

        feasible_cells += 1
        if first_witness is None:
            first_witness = combo
            witness_event_details = event_details

        for e, (co_idx, pair_idx, pair_freqs) in enumerate(combo_causes):
            feasible_cochannel[e].update(co_idx)
            feasible_pairs[e].update(pair_idx)
            bucket = pair_realizations[e]
            for key, freqs in pair_freqs.items():
                bucket.setdefault(key, set()).add(freqs)

    feasible = first_witness is not None

    # -- Choose the concrete witness offsets --------------------------------
    selected_offsets_by_id: dict[str, int] = {}
    offset_assignment: list[dict[str, Any]] = []
    witness_offsets_by_index: dict[int, int] = {}
    if feasible:
        assert first_witness is not None
        for dim, mic_index in enumerate(variable_positions):
            value = dimension_cells[dim][first_witness[dim]]
            witness_offsets_by_index[mic_index] = value

    for mic_index, mic in enumerate(mics):
        is_variable = mic_index in mic_dimension
        if feasible:
            value: int | None = (
                witness_offsets_by_index[mic_index]
                if is_variable
                else fixed_offsets[mic_index]
            )
        elif is_variable:
            value = None
        else:
            value = fixed_offsets[mic_index]

        if is_variable:
            source = "range_solver"
        elif mic.get("offset_range") is not None:
            source = "fixed_offset"
        else:
            source = "default_zero"

        selected_offsets_by_id[mic["mic_id"]] = value if feasible else 0
        entry: dict[str, Any] = {
            "mic_id": mic["mic_id"],
            "offset": value,
            "clock_offset": value,
            "source": source,
            "clock_offset_range": (
                list(mic["offset_range"]) if mic.get("offset_range") is not None else None
            ),
        }
        offset_assignment.append(entry)

    # -- Build JSON-friendly cause records -----------------------------------
    def cochannel_record(mic_index: int, e: int | None) -> dict[str, Any]:
        mic = mics[mic_index]
        freq_segments = [
            [seg["start"], seg["end"]]
            for seg in mic["segments"]
            if int(seg["frequency"]) == target_frequency
        ]
        return {
            "conflict_type": "cochannel",
            "code": "duplicate_frequency",
            "mic_id": mic["mic_id"],
            "id": mic["mic_id"],
            "mic_ids": [mic["mic_id"]],
            "name": mic.get("name"),
            "mic_name": mic.get("name"),
            "frequency": target_frequency,
            "target_frequency": target_frequency,
            "possible_segments": freq_segments,
        }

    def pair_record(
        key: tuple[int, int],
        realizations: set[tuple[int, int]] | None,
    ) -> dict[str, Any]:
        a_index, b_index = key
        a_mic, b_mic = mics[a_index], mics[b_index]
        if realizations:
            freq_pairs = sorted(realizations)
            a_freq, b_freq = freq_pairs[0]
        else:
            a_freq = b_freq = None
            freq_pairs = []
        a_id, b_id = a_mic["mic_id"], b_mic["mic_id"]
        return {
            "conflict_type": "intermodulation_2a_minus_b",
            "code": "reflection_hits_target_frequency",
            "a_mic_id": a_id,
            "b_mic_id": b_id,
            "a_mic": a_id,
            "b_mic": b_id,
            "a_mic_name": a_mic.get("name"),
            "b_mic_name": b_mic.get("name"),
            "mic_ids": [a_id, b_id],
            "pair": [a_id, b_id],
            "unordered_pair": sorted([a_id, b_id]),
            "a_frequency": a_freq,
            "b_frequency": b_freq,
            "forbidden_value": (
                2 * a_freq - b_freq if a_freq is not None else target_frequency
            ),
            "intermod_frequency": target_frequency,
            "target_frequency": target_frequency,
            "equation": "2*a-b=target_frequency",
            "frequency_realizations": [
                {"a_frequency": fa, "b_frequency": fb, "product": 2 * fa - fb}
                for fa, fb in freq_pairs
            ],
        }

    event_results: list[dict[str, Any]] = []
    dropout_results: list[dict[str, Any]] = []
    for e, event in enumerate(events):
        if feasible:
            co_ids = sorted(feasible_cochannel[e])
            pair_keys = sorted(feasible_pairs[e], key=lambda k: (k[0], k[1]))
            co_records = [cochannel_record(i, e) for i in co_ids]
            pair_records = [
                pair_record(key, pair_realizations[e].get(key)) for key in pair_keys
            ]
            co_id_list = [mics[i]["mic_id"] for i in co_ids]
            pair_id_list = [[mics[a]["mic_id"], mics[b]["mic_id"]] for a, b in pair_keys]
        else:
            co_records = []
            pair_records = []
            co_id_list = []
            pair_id_list = []

        domain_co_records = [
            cochannel_record(i, e) for i in sorted(domain_cochannel[e])
        ]
        domain_pair_records = [
            pair_record(key, domain_pair_freqs[e].get(key))
            for key in sorted(domain_pairs[e], key=lambda k: (k[0], k[1]))
        ]

        at_selected: dict[str, Any] | None = None
        if feasible and witness_event_details is not None:
            detail = witness_event_details[e]
            witness_co = [
                cochannel_record(i, e) for i in detail["cochannel_indexes"]
            ]
            witness_pairs = [
                pair_record(key, {detail["pair_freqs"][key]})
                for key in detail["pair_indexes"]
            ]
            at_selected = {
                "applied_clock_offsets": dict(selected_offsets_by_id),
                "active_transmissions": detail["active"],
                "conflict_detected": detail["conflict"],
                "cochannel_mics": witness_co,
                "intermod_pairs": witness_pairs,
            }

        result_row = {
            "event_index": e,
            "index": e,
            "time": event["time"],
            "timestamp": event["time"],
            "label": event.get("label"),
            "status": event["status"],
            "observed_status": event["status"],
            "is_dropout": event["is_dropout"],
            # Candidates over ALL globally feasible offset assignments.
            "cochannel_mics": co_records,
            "co_channel_mics": co_records,
            "cochannel_sources": co_records,
            "duplicate_frequency_mics": co_records,
            "duplicate_mics": co_records,
            "repeated_frequency_mics": co_records,
            "cochannel_mic_ids": co_id_list,
            "cochannel_ids": co_id_list,
            "co_channel_mic_ids": co_id_list,
            "co_channel_ids": co_id_list,
            "duplicate_mic_ids": co_id_list,
            "intermod_pairs": pair_records,
            "intermodulation_pairs": pair_records,
            "im_pairs": pair_records,
            "intermod_pair_details": pair_records,
            "intermod_mic_pairs": pair_id_list,
            "intermodulation_mic_pairs": pair_id_list,
            "intermod_pair_ids": pair_id_list,
            "pair_ids": pair_id_list,
            "candidate_causes": {
                "cochannel_mics": co_records,
                "intermod_pairs": pair_records,
                "cochannel_mic_ids": co_id_list,
                "intermod_mic_pairs": pair_id_list,
                "total": len(co_records) + len(pair_records),
            },
            "candidate_count": len(co_records) + len(pair_records),
            # What the single returned witness assignment produces.
            "at_selected_offsets": at_selected,
            # Whole-range diagnostics (only interesting for no_solution).
            "domain_possible_causes": {
                "cochannel_mics": domain_co_records,
                "intermod_pairs": domain_pair_records,
            },
        }
        if feasible:
            result_row["explained"] = True
            result_row["satisfied"] = witness_event_details[e]["satisfied"]
        event_results.append(result_row)
        if event["is_dropout"]:
            dropout_results.append(result_row)

    range_spans = [hi - lo for lo, hi in dim_bounds]

    # Deduplicated answer to "which transmitter mics should the crew check".
    mics_to_check: list[str] = []
    check_reasons: dict[str, dict[str, Any]] = {}

    def _register_check(mic_id: str, reason: str, event_index: int) -> None:
        if mic_id not in check_reasons:
            check_reasons[mic_id] = {
                "mic_id": mic_id,
                "reasons": set(),
                "event_indexes": set(),
            }
            mics_to_check.append(mic_id)
        check_reasons[mic_id]["reasons"].add(reason)
        check_reasons[mic_id]["event_indexes"].add(event_index)

    for row in dropout_results:
        e = row["event_index"]
        if feasible:
            for record in row["cochannel_mics"]:
                _register_check(record["mic_id"], "cochannel", e)
            for record in row["intermod_pairs"]:
                _register_check(record["a_mic_id"], "intermodulation_2a_minus_b", e)
                _register_check(record["b_mic_id"], "intermodulation_2a_minus_b", e)

    suspects = []
    for mic_id in mics_to_check:
        details = check_reasons[mic_id]
        name = next((m.get("name") for m in mics if m["mic_id"] == mic_id), None)
        suspects.append(
            {
                "mic_id": mic_id,
                "name": name,
                "reasons": sorted(details["reasons"]),
                "event_indexes": sorted(details["event_indexes"]),
            }
        )

    solver_info = {
        "algorithm": "endpoint_compressed_integer_offset_grid",
        "offset_variables": len(dim_bounds),
        "offset_range_spans_ms": range_spans,
        "cut_counts": cut_counts,
        "atomic_cells_per_dimension": [len(cells) for cells in dimension_cells],
        "assignments_checked": len(index_combos),
        "feasible_cells": feasible_cells,
        "iterates_over_millisecond_span": False,
        "complexity": "O(C1*C2*E*(M*S+M^2)); C<=2+2*E*S, independent of range span",
    }

    if feasible:
        solution = {
            "clock_offsets": selected_offsets_by_id,
            "offsets": selected_offsets_by_id,
            "offset_assignment": offset_assignment,
            "assignments": offset_assignment,
        }
        return {
            "status": "feasible",
            "review_status": "explainable",
            "outcome": "explainable",
            "feasible": True,
            "has_solution": True,
            "solution_found": True,
            "target_mic_id": target_id,
            "target_frequency": target_frequency,
            "clock_offsets": selected_offsets_by_id,
            "offsets": selected_offsets_by_id,
            "offset_assignment": offset_assignment,
            "solution": solution,
            "events": event_results,
            "event_results": event_results,
            "results": event_results,
            "dropouts": dropout_results,
            "reviews": dropout_results,
            "dropout_reviews": dropout_results,
            "mics_to_check": mics_to_check,
            "suspect_mic_ids": mics_to_check,
            "suspects": suspects,
            "no_solution": None,
            "solver": solver_info,
            "message": "已找到能解释整场记录的一组时钟偏移，并逐次汇总了所有可行对时下的可能成因。",
        }

    no_solution = {
        "code": "no_offset_assignment_explains_session",
        "message": "整场记录对不上：不存在同时满足全部掉音须命中、正常须无冲突的时钟偏移组合。",
        "domain_possible_dropout_events": [
            {
                "event_index": e,
                "time": event["time"],
                "cochannel_mic_ids": [mics[i]["mic_id"] for i in sorted(domain_cochannel[e])],
                "intermod_pairs": [
                    [mics[a]["mic_id"], mics[b]["mic_id"]]
                    for a, b in sorted(domain_pairs[e])
                ],
            }
            for e, event in enumerate(events)
            if event["is_dropout"]
        ],
    }
    domain_mics_to_check: list[str] = []
    for item in no_solution["domain_possible_dropout_events"]:
        for mic_id in item["cochannel_mic_ids"]:
            if mic_id not in domain_mics_to_check:
                domain_mics_to_check.append(mic_id)
        for pair in item["intermod_pairs"]:
            for mic_id in pair:
                if mic_id not in domain_mics_to_check:
                    domain_mics_to_check.append(mic_id)
    no_solution["domain_mics_to_check"] = domain_mics_to_check

    return {
        "status": "infeasible",
        "review_status": "no_solution",
        "outcome": "no_solution",
        "feasible": False,
        "has_solution": False,
        "solution_found": False,
        "target_mic_id": target_id,
        "target_frequency": target_frequency,
        "clock_offsets": None,
        "offsets": None,
        "offset_assignment": offset_assignment,
        "solution": None,
        "events": event_results,
        "event_results": event_results,
        "results": event_results,
        "dropouts": dropout_results,
        "reviews": dropout_results,
        "dropout_reviews": dropout_results,
        "mics_to_check": [],
        "suspect_mic_ids": [],
        "suspects": [],
        "domain_mics_to_check": domain_mics_to_check,
        "no_solution": no_solution,
        "solver": solver_info,
        "message": no_solution["message"],
    }


# Convenience aliases for different naming preferences.
solve_review = review_incident
analyze_dropouts = review_incident
review_dropout_incident = review_incident
solve_dropout_review = review_incident
review_session = review_incident
analyze_session = review_incident
forensics_review = review_incident
