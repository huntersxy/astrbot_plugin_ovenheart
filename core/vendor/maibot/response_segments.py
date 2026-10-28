"""Original MaiBot reply-segment compression; see response_source.json."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ProcessedResponseSegment:
    """回复后处理产生的单条消息及其发送提示。"""

    text: str
    quote_previous: bool = False
    # 原始文本中紧跟本段的分隔符；多条消息被压缩合并为一条时，用它恢复句间的停顿
    separator: str = ""

def _merge_processed_segments_to_max_count(
    segments: list[ProcessedResponseSegment],
    max_count: int,
) -> list[ProcessedResponseSegment]:
    """压缩回复段数量，并优先让需要引用的纠正内容保持在消息开头。"""

    if len(segments) <= max_count:
        return segments
    if max_count <= 0:
        return []

    segment_count = len(segments)
    required_starts = [index for index, segment in enumerate(segments) if index > 0 and segment.quote_previous]
    group_starts = {0, *required_starts[: max_count - 1]}

    # 在保留纠正消息边界后，沿用原来的均匀分组策略填充其余可用分组。
    evenly_spaced_starts: list[int] = []
    start_index = 0
    for group_index in range(max_count):
        remaining_segments = segment_count - start_index
        remaining_groups = max_count - group_index
        group_size = (remaining_segments + remaining_groups - 1) // remaining_groups
        evenly_spaced_starts.append(start_index)
        start_index += group_size

    for candidate_start in evenly_spaced_starts:
        if len(group_starts) >= max_count:
            break
        group_starts.add(candidate_start)

    sorted_starts = sorted(group_starts)
    merged_segments: list[ProcessedResponseSegment] = []
    for group_index, group_start in enumerate(sorted_starts):
        group_end = sorted_starts[group_index + 1] if group_index + 1 < len(sorted_starts) else segment_count
        group = segments[group_start:group_end]
        # 组内多段被压缩为一条消息时，补回各段尾随的分隔符，避免多句硬拼接成无标点的一整坨
        merged_text_parts: list[str] = []
        for position, segment in enumerate(group):
            merged_text_parts.append(segment.text)
            if position < len(group) - 1:
                merged_text_parts.append(segment.separator)
        merged_segments.append(
            ProcessedResponseSegment(
                text="".join(merged_text_parts),
                quote_previous=group[0].quote_previous,
                separator=group[-1].separator,
            )
        )

    return merged_segments

