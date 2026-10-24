"""
Generates synthetic test images into samples/ for test_pipeline_sample.py,
so the AI pipeline (STT + OCR + VLM) can be exercised without live camera/
mic hardware.

Usage:
    python samples/generate_samples.py
"""

import os

from PIL import Image, ImageDraw, ImageFont

SAMPLES_DIR = os.path.dirname(os.path.abspath(__file__))


def _font(size: int) -> ImageFont.ImageFont:
    # Try a couple of common macOS system fonts that support Korean; fall
    # back to PIL's built-in bitmap font (English-only) if none are found.
    candidates = [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_korean_subway_sign() -> Image.Image:
    img = Image.new("RGB", (800, 400), color=(10, 90, 40))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 780, 380], outline=(255, 255, 255), width=4)
    draw.text((60, 60), "2호선", fill=(255, 255, 255), font=_font(72))
    draw.text((60, 160), "시청 방면", fill=(255, 255, 255), font=_font(48))
    draw.text((60, 250), "3번 출구", fill=(255, 255, 0), font=_font(56))
    return img


def make_english_exit_sign() -> Image.Image:
    img = Image.new("RGB", (800, 400), color=(20, 20, 20))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 780, 380], outline=(255, 255, 255), width=4)
    draw.text((60, 80), "EXIT 5", fill=(0, 255, 0), font=_font(90))
    draw.text((60, 220), "Platform 2, Uptown", fill=(255, 255, 255), font=_font(48))
    return img


def make_red_mug_on_table() -> Image.Image:
    img = Image.new("RGB", (800, 400), color=(210, 190, 160))  # wood-ish background
    draw = ImageDraw.Draw(img)
    draw.ellipse([300, 150, 460, 310], fill=(200, 30, 30))  # red mug body
    draw.rectangle([440, 190, 500, 260], outline=(200, 30, 30), width=12)  # handle
    return img


def make_empty_hallway() -> Image.Image:
    img = Image.new("RGB", (800, 400), color=(235, 235, 230))
    draw = ImageDraw.Draw(img)
    draw.polygon([(0, 400), (300, 150), (500, 150), (800, 400)], fill=(200, 200, 195))
    return img


def make_person_in_hallway() -> Image.Image:
    # same hallway background as make_empty_hallway, plus a simple person
    # silhouette -- for presence/absence contrast testing
    img = make_empty_hallway()
    draw = ImageDraw.Draw(img)
    # head
    draw.ellipse([370, 190, 430, 250], fill=(40, 40, 40))
    # body
    draw.polygon([(360, 250), (440, 250), (455, 400), (345, 400)], fill=(40, 40, 40))
    return img


def make_desk_scene() -> Image.Image:
    # a few distinct objects on a desk, for scene-description testing
    img = Image.new("RGB", (800, 400), color=(225, 215, 195))  # desk surface
    draw = ImageDraw.Draw(img)
    # laptop (open, wedge shape)
    draw.polygon([(80, 280), (320, 280), (280, 140), (120, 140)], fill=(60, 60, 65))
    draw.rectangle([80, 280, 320, 300], fill=(40, 40, 45))
    # mug
    draw.ellipse([420, 220, 500, 300], fill=(200, 30, 30))
    draw.rectangle([490, 240, 530, 280], outline=(200, 30, 30), width=8)
    # book (closed, lying flat)
    draw.rectangle([560, 260, 740, 320], fill=(30, 60, 140))
    draw.rectangle([560, 260, 740, 270], fill=(50, 90, 180))
    return img


def make_prescription_label_en() -> Image.Image:
    # a simplified pharmacy prescription label -- for the medical-record route
    img = Image.new("RGB", (800, 400), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 790, 390], outline=(0, 0, 0), width=3)
    draw.text((40, 30), "Riverside Pharmacy", fill=(0, 0, 0), font=_font(36))
    draw.text((40, 90), "Metformin 500mg", fill=(0, 0, 0), font=_font(44))
    draw.text((40, 150), "Take 1 tablet by mouth twice daily", fill=(0, 0, 0), font=_font(30))
    draw.text((40, 200), "with food", fill=(0, 0, 0), font=_font(30))
    draw.text((40, 260), "Qty: 60   Refills: 2", fill=(0, 0, 0), font=_font(28))
    draw.text((40, 310), "Dr. A. Lee -- Exp: 2027-03-01", fill=(0, 0, 0), font=_font(26))
    return img


def make_prescription_label_ko() -> Image.Image:
    img = Image.new("RGB", (800, 400), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 790, 390], outline=(0, 0, 0), width=3)
    draw.text((40, 30), "행복약국", fill=(0, 0, 0), font=_font(40))
    draw.text((40, 100), "메트포르민 500mg", fill=(0, 0, 0), font=_font(40))
    draw.text((40, 170), "하루 두 번, 식후 1정씩 복용", fill=(0, 0, 0), font=_font(34))
    draw.text((40, 240), "수량: 60정   재처방: 2회", fill=(0, 0, 0), font=_font(28))
    draw.text((40, 300), "이재현 원장 -- 유효기간: 2027-03-01", fill=(0, 0, 0), font=_font(24))
    return img


def make_bus_schedule_sign_en() -> Image.Image:
    # public-transit sample distinct from the subway/exit signs -- a bus stop schedule
    img = Image.new("RGB", (800, 400), color=(15, 40, 90))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 780, 380], outline=(255, 255, 255), width=4)
    draw.text((60, 50), "BUS 42", fill=(255, 220, 0), font=_font(80))
    draw.text((60, 160), "Downtown / Main St", fill=(255, 255, 255), font=_font(40))
    draw.text((60, 230), "Next arrival: 6 min", fill=(255, 255, 255), font=_font(36))
    draw.text((60, 290), "Gate 3", fill=(255, 255, 255), font=_font(36))
    return img


SAMPLES = {
    "korean_subway_sign.png": make_korean_subway_sign,
    "english_exit_sign.png": make_english_exit_sign,
    "red_mug_on_table.png": make_red_mug_on_table,
    "empty_hallway.png": make_empty_hallway,
    "person_in_hallway.png": make_person_in_hallway,
    "desk_scene.png": make_desk_scene,
    "prescription_label_en.png": make_prescription_label_en,
    "prescription_label_ko.png": make_prescription_label_ko,
    "bus_schedule_sign_en.png": make_bus_schedule_sign_en,
}


def main() -> None:
    for filename, builder in SAMPLES.items():
        path = os.path.join(SAMPLES_DIR, filename)
        builder().save(path)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
