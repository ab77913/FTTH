from io import BytesIO
from PIL import Image


def generate_ocr_crops(image_bytes):

    img = Image.open(BytesIO(image_bytes))

    width, height = img.size

    crops = []

    # ── Center crop ─────────────────────────────────────────

    center_crop = img.crop((
        width * 0.25,
        height * 0.25,
        width * 0.75,
        height * 0.75
    ))

    crops.append(("center_crop", center_crop))

    # ── Lower-center crop (mailbox / entrance area) ────────

    lower_crop = img.crop((
        width * 0.20,
        height * 0.50,
        width * 0.80,
        height * 0.95
    ))

    crops.append(("lower_crop", lower_crop))

    # ── Right-side crop ─────────────────────────────────────

    right_crop = img.crop((
        width * 0.55,
        height * 0.20,
        width * 0.95,
        height * 0.85
    ))

    crops.append(("right_crop", right_crop))

    crop_bytes = []

    for crop_name, crop_img in crops:

        buf = BytesIO()

        crop_img.save(buf, format="JPEG")

        crop_bytes.append(
            (crop_name, buf.getvalue())
        )

    return crop_bytes