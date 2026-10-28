"""Transport-only static-image conversion; keep original assets for memory."""

from io import BytesIO


def single_frame_gif(payload):
    from PIL import Image as PILImage
    from PIL import ImageOps

    with PILImage.open(BytesIO(payload)) as source:
        if source.format == "GIF" or getattr(source, "n_frames", 1) > 1:
            return None
        image = ImageOps.exif_transpose(source).convert("RGBA")
        alpha = image.getchannel("A")
        indexed = image.convert("RGB").quantize(colors=255)
        output = BytesIO()
        if alpha.getextrema()[0] < 255:
            indexed.paste(
                255, mask=alpha.point(lambda value: 255 if value < 128 else 0)
            )
            indexed.save(output, format="GIF", transparency=255, save_all=False)
        else:
            indexed.save(output, format="GIF", save_all=False)
        return output.getvalue()
