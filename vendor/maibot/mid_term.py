"""Original MaiBot mid-term pure algorithms; see mid_term_source.json and NOTICE.md."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from math import sqrt
from typing import Any, Sequence
from astrbot.api import logger

from json_repair import repair_json
from pydantic import BaseModel

MID_TERM_MEMORY_REFERENCE_MARKER = "【聊天回想-内部参考】"
MID_TERM_MEMORY_RECALL_SUMMARY_TEXT_LIMIT = 1400
MID_TERM_MEMORY_DEFAULT_RECALL_THRESHOLD = 0.8

# Native internal-reference identity; this is not a platform message.
@dataclass(frozen=True, slots=True)
class MemorySummaryMessage:
    message_id: str


class MidTermMemorySummaryModel(BaseModel):
    """聊天记录压缩摘要。"""

    summary: str
    recall_cues: list[str] = []


@dataclass(frozen=True, slots=True)
class MidTermMemoryRecallCandidate:
    """一条聊天回想匹配段的召回候选。"""

    message: MemorySummaryMessage
    payload: dict[str, Any]
    segment_text: str
    score: float


def _parse_summary_response(response: str) -> MidTermMemorySummaryModel | None:
    payload = _load_json_payload(response)
    if not isinstance(payload, dict):
        return None

    summary = _resolve_payload_summary(payload)
    recall_cues = _normalize_recall_cues(payload.get("recall_cues") or payload.get("match_segments"))
    if not summary:
        return None
    return MidTermMemorySummaryModel(
        summary=summary,
        recall_cues=recall_cues,
    )


def _load_json_payload(response: str) -> Any:
    normalized_response = str(response or "").strip()
    if not normalized_response:
        return None

    candidates = [normalized_response]
    if fence_match := re.search(r"```(?:json)?\s*(.*?)\s*```", normalized_response, flags=re.S | re.I):
        candidates.append(fence_match.group(1).strip())

    object_start = normalized_response.find("{")
    object_end = normalized_response.rfind("}")
    if object_start >= 0 and object_end > object_start:
        candidates.append(normalized_response[object_start : object_end + 1])

    seen_candidates: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        parsed_payload = _parse_json_candidate(candidate)
        if isinstance(parsed_payload, dict):
            return parsed_payload

    return None


def _parse_json_candidate(candidate: str) -> Any:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    try:
        return repair_json(candidate, return_objects=True, logging=False)
    except Exception:
        return None


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    normalized_values: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            normalized_values.append(text)
    return normalized_values


def _normalize_recall_cues(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_cues = re.split(r"\n+", value)
    elif isinstance(value, list):
        raw_cues = value
    else:
        raw_cues = []

    cues: list[str] = []
    seen: set[str] = set()
    for raw_cue in raw_cues:
        cue = _normalize_recall_cue_text(raw_cue)
        if not cue or cue in seen:
            continue
        seen.add(cue)
        cues.append(cue)
    return cues[:5]


def _normalize_recall_cue_text(value: Any) -> str:
    if isinstance(value, dict):
        text = value.get("text") or value.get("query") or value.get("content") or ""
        associations = _coerce_str_list(value.get("associations"))
        reason = str(value.get("reason") or "").strip()
        parts = [str(text or "").strip()]
        if associations:
            parts.append(f"关联: {'、'.join(associations)}")
        if reason:
            parts.append(f"原因: {reason}")
        return "；".join(part for part in parts if part)
    return " ".join(str(value or "").split()).strip()


def _resolve_payload_summary(payload: dict[str, Any]) -> str:
    return str(payload.get("summary") or payload.get("long_summary") or payload.get("brief") or "").strip()


def _iter_recall_cue_payloads(payload: dict[str, Any]) -> list[Any]:
    recall_cues = payload.get("recall_cues")
    if isinstance(recall_cues, list) and recall_cues:
        return recall_cues
    match_segments = payload.get("match_segments")
    return match_segments if isinstance(match_segments, list) else []


def _is_mid_term_memory_candidate_already_recalled(
    candidate: MidTermMemoryRecallCandidate,
    *,
    recalled_keys: set[tuple[str, str]],
    recalled_segments: set[str],
) -> bool:
    normalized_segment = _normalize_reference_identity_text(candidate.segment_text)
    if not normalized_segment:
        return False

    message_id = _normalize_reference_identity_text(candidate.message.message_id)
    if message_id and (message_id, normalized_segment) in recalled_keys:
        return True
    return normalized_segment in recalled_segments


def _normalize_reference_identity_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _select_best_recall_candidate(
    candidates: Sequence[MidTermMemoryRecallCandidate],
    *,
    query_embedding: Sequence[float],
    threshold: float,
) -> MidTermMemoryRecallCandidate | None:
    best_candidate: MidTermMemoryRecallCandidate | None = None
    for candidate in candidates:
        segment_embedding = _get_candidate_embedding(candidate.payload, candidate.segment_text)
        if not segment_embedding:
            continue
        score = _cosine_similarity(query_embedding, segment_embedding)
        scored_candidate = MidTermMemoryRecallCandidate(
            message=candidate.message,
            payload=candidate.payload,
            segment_text=candidate.segment_text,
            score=score,
        )
        if best_candidate is None or scored_candidate.score > best_candidate.score:
            best_candidate = scored_candidate

    if best_candidate is None or best_candidate.score <= threshold:
        return None
    return best_candidate


def _get_candidate_embedding(payload: dict[str, Any], segment_text: str) -> list[float]:
    for cue_payload in _iter_recall_cue_payloads(payload):
        if not isinstance(cue_payload, dict):
            continue
        if str(cue_payload.get("text") or "").strip() != segment_text:
            continue
        embedding = cue_payload.get("embedding")
        if not isinstance(embedding, list):
            return []
        return [float(value) for value in embedding]
    return []


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError(f"聊天回想召回 embedding 维度不一致: query={len(left)} segment={len(right)}")
    if not left:
        return 0.0

    dot_product = sum(
        float(left_value) * float(right_value) for left_value, right_value in zip(left, right, strict=True)
    )
    left_norm = sqrt(sum(float(value) * float(value) for value in left))
    right_norm = sqrt(sum(float(value) * float(value) for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot_product / (left_norm * right_norm)


def _format_mid_term_memory_reference(candidate: MidTermMemoryRecallCandidate) -> str:
    payload = candidate.payload
    message_id = str(candidate.message.message_id or "").strip()
    time_range = str(payload.get("time_range") or "未知").strip()
    participants = _coerce_str_list(payload.get("participants"))
    summary = _resolve_payload_summary(payload) or "无"
    if len(summary) > MID_TERM_MEMORY_RECALL_SUMMARY_TEXT_LIMIT:
        summary = summary[:MID_TERM_MEMORY_RECALL_SUMMARY_TEXT_LIMIT].rstrip() + "..."

    return "\n".join(
        [
            MID_TERM_MEMORY_REFERENCE_MARKER,
            "以下是根据当前上下文匹配到的一条聊天回想，只作为内部参考；仅在自然相关时使用，不要生硬复述。",
            *([f"摘要ID: {message_id}"] if message_id else []),
            f"匹配分数: {candidate.score:.4f}",
            f"匹配段: {candidate.segment_text}",
            f"时间范围: {time_range}",
            f"参与人物: {'、'.join(participants) if participants else '未知'}",
            "",
            "summary:",
            summary,
        ]
    ).strip()


def _build_summary_message_id(
    *,
    timestamp: datetime,
    time_range: str,
    participants: Sequence[str],
    summary: str,
) -> str:
    digest_source = "\n".join([time_range, "、".join(participants), summary])
    digest = sha1(digest_source.encode("utf-8")).hexdigest()[:8]
    return f"mtm:{_to_base36(int(timestamp.timestamp() * 1000))}:{digest}"


def _to_base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    normalized_value = max(0, int(value))
    if normalized_value == 0:
        return "0"

    digits: list[str] = []
    while normalized_value:
        normalized_value, remainder = divmod(normalized_value, 36)
        digits.append(alphabet[remainder])
    return "".join(reversed(digits))


def _truncate(text: str, max_length: int) -> str:
    normalized_text = str(text or "").strip()
    if len(normalized_text) <= max_length:
        return normalized_text
    return normalized_text[:max_length] + "..."

