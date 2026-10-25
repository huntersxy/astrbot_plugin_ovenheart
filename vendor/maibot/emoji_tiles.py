"""Original MaiBot emoji contact-sheet functions; see emoji_source.json and NOTICE.md."""
from __future__ import annotations
import math
from io import BytesIO
from typing import List
from PIL import Image as PILImage, ImageDraw, ImageFont
from pydantic import BaseModel, Field as PydanticField

_EMOJI_CANDIDATE_TILE_SIZE = 256
_EMOJI_INDEX_BADGE_SIZE = 64
_EMOJI_INDEX_FONT_SIZE = 32


class EmojiSelectionResult(BaseModel):
    """表情包子代理的结构化选择结果。"""

    emoji_index: int = PydanticField(default=1, description="选中的表情包序号，从 1 开始计数。")
    reason: str = PydanticField(default="", description="选择这张表情包的简短理由。")


def _calculate_grid_shape(candidate_count: int) -> tuple[int, int]:
    """根据候选数量计算尽量接近矩形的拼图行列数。"""

    if candidate_count <= 0:
        return 1, 1

    best_columns = candidate_count
    best_rows = 1
    best_score: tuple[int, int] | None = None

    for columns in range(1, candidate_count + 1):
        rows = math.ceil(candidate_count / columns)
        empty_slots = rows * columns - candidate_count
        aspect_gap = abs(columns - rows)
        score = (aspect_gap, empty_slots)
        if best_score is None or score < best_score:
            best_score = score
            best_columns = columns
            best_rows = rows

    return best_rows, best_columns


def _build_placeholder_tile(label: str, tile_size: int) -> PILImage.Image:
    """构建图片读取失败时使用的占位图。"""

    tile = PILImage.new("RGB", (tile_size, tile_size), color=(245, 245, 245))
    draw = ImageDraw.Draw(tile)
    font = ImageFont.load_default()
    text_bbox = draw.textbbox((0, 0), label, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    draw.text(
        ((tile_size - text_width) / 2, (tile_size - text_height) / 2),
        label,
        fill=(80, 80, 80),
        font=font,
    )
    return tile


def _build_labeled_tile(image_bytes: bytes, index: int, tile_size: int) -> PILImage.Image:
    """构建带序号角标的候选图片块。"""

    try:
        with PILImage.open(BytesIO(image_bytes)) as raw_image:
            image = raw_image.convert("RGBA")
    except Exception:
        return _build_placeholder_tile(str(index), tile_size)

    image.thumbnail((tile_size, tile_size))
    tile = PILImage.new("RGBA", (tile_size, tile_size), color=(255, 255, 255, 255))
    offset_x = (tile_size - image.width) // 2
    offset_y = (tile_size - image.height) // 2
    tile.paste(image, (offset_x, offset_y), image)

    draw = ImageDraw.Draw(tile)
    font = ImageFont.load_default(size=_EMOJI_INDEX_FONT_SIZE)
    badge_size = _EMOJI_INDEX_BADGE_SIZE
    badge_margin = 14
    label = str(index)
    text_bbox = draw.textbbox((0, 0), label, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    # 全局递增的编号可能超过两位；角标随文字扩宽，超长编号缩小字体。
    while text_width > tile_size - 2 * badge_margin - 12 and font.size > 8:
        font = ImageFont.load_default(size=font.size - 1)
        text_bbox = draw.textbbox((0, 0), label, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
    badge_width = max(badge_size, text_width + 12)
    draw.rounded_rectangle(
        (
            badge_margin,
            badge_margin,
            badge_margin + badge_width,
            badge_margin + badge_size,
        ),
        radius=8,
        fill=(0, 0, 0, 180),
    )
    draw.text(
        (
            badge_margin + (badge_width - text_width) / 2,
            badge_margin + (badge_size - text_height) / 2 - 1,
        ),
        label,
        fill=(255, 255, 255, 255),
        font=font,
    )
    return tile


def _merge_emoji_tiles(image_bytes_list: List[bytes], start_index: int = 1) -> bytes:
    """将候选表情图拼接成一张尽量接近矩形的网格图片。"""

    tile_size = _EMOJI_CANDIDATE_TILE_SIZE
    gap = 12
    candidate_count = len(image_bytes_list)
    grid_rows, grid_columns = _calculate_grid_shape(candidate_count)
    tiles = [
        _build_labeled_tile(image_bytes=image_bytes, index=index, tile_size=tile_size)
        for index, image_bytes in enumerate(image_bytes_list, start=start_index)
    ]
    canvas_width = tile_size * grid_columns + gap * (grid_columns - 1)
    canvas_height = tile_size * grid_rows + gap * (grid_rows - 1)
    canvas = PILImage.new("RGBA", (canvas_width, canvas_height), color=(255, 255, 255, 255))

    for index, tile in enumerate(tiles):
        row = index // grid_columns
        column = index % grid_columns
        offset_x = column * (tile_size + gap)
        offset_y = row * (tile_size + gap)
        canvas.paste(tile, (offset_x, offset_y), tile)

    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG")
    return output.getvalue()

