from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageOps


ALLOWED_RESIZE_MODES = {"contain", "cover"}
ALLOWED_OUTPUT_SIZE_MODES = {"match_input", "fixed"}


def match_image_size(
    output_image_path: Path,
    reference_image_path: Path,
    mode: str = "contain",
    output_size_mode: str = "match_input",
    required_width: int | None = None,
    required_height: int | None = None,
    background=(255, 255, 255),
) -> dict:
    resize_mode = (mode or "contain").strip().lower()
    if resize_mode not in ALLOWED_RESIZE_MODES:
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported output resize mode: {mode}. Expected contain or cover.",
        )
    size_mode = (output_size_mode or "match_input").strip().lower()
    if size_mode not in ALLOWED_OUTPUT_SIZE_MODES:
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported output size mode: {output_size_mode}. Expected match_input or fixed.",
        )

    if not output_image_path.exists():
        raise HTTPException(status_code=404, detail="generated output image not found for post-processing")
    if not reference_image_path.exists():
        raise HTTPException(status_code=404, detail="reference floorplan image not found for post-processing")

    try:
        with Image.open(reference_image_path) as reference_image:
            reference_width, reference_height = reference_image.size
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to read reference image for size matching: {exc}") from exc

    if size_mode == "fixed":
        if not required_width or not required_height or required_width <= 0 or required_height <= 0:
            raise HTTPException(status_code=500, detail="fixed output size requires positive OUTPUT_WIDTH and OUTPUT_HEIGHT")
        target_size = (int(required_width), int(required_height))
    else:
        target_size = (reference_width, reference_height)

    try:
        with Image.open(output_image_path) as output_image:
            original_output_width, original_output_height = output_image.size
            prepared_output = _prepare_image(output_image)

            if resize_mode == "cover":
                resized_output = ImageOps.fit(prepared_output, target_size, method=Image.Resampling.LANCZOS)
            else:
                resized_content = ImageOps.contain(prepared_output, target_size, method=Image.Resampling.LANCZOS)
                resized_output = Image.new("RGB", target_size, color=background)
                offset_x = (target_size[0] - resized_content.width) // 2
                offset_y = (target_size[1] - resized_content.height) // 2
                resized_output.paste(resized_content, (offset_x, offset_y))

            resized_output.save(output_image_path, format="PNG")
            final_output_width, final_output_height = resized_output.size
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to resize generated output image: {exc}") from exc

    return {
        "output_size_mode": size_mode,
        "required_width": target_size[0],
        "required_height": target_size[1],
        "reference_width": reference_width,
        "reference_height": reference_height,
        "original_output_width": original_output_width,
        "original_output_height": original_output_height,
        "final_output_width": final_output_width,
        "final_output_height": final_output_height,
        "resize_mode": resize_mode,
    }


def _prepare_image(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"}:
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        background.alpha_composite(image.convert("RGBA"))
        return background.convert("RGB")
    if image.mode == "P":
        converted = image.convert("RGBA")
        background = Image.new("RGBA", converted.size, (255, 255, 255, 255))
        background.alpha_composite(converted)
        return background.convert("RGB")
    if image.mode not in {"RGB"}:
        return image.convert("RGB")
    return image.copy()
