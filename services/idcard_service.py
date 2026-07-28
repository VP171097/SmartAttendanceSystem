"""
Student ID card generator.

Produces a print-ready CR80 card (85.6 x 54 mm at 300 DPI) with the college
branding, the student's photograph, their key details and a Code 39 barcode of
the enrollment number.

The barcode is drawn from first principles with Pillow rather than pulling in a
barcode dependency -- Code 39 is a simple, self-checking symbology and this
keeps the requirements list short.

**The barcode is for the library/office desk, not for attendance.**  Attendance
in this system is face recognition only, as the specification requires.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config.settings import ID_CARD_DIR, config
from core.logger import get_logger
from core.validators import safe_filename

logger = get_logger("services.idcard")

# CR80 at 300 DPI -- the standard plastic card size.
DPI = 300
CARD_W = int(85.6 / 25.4 * DPI)      # 1011 px
CARD_H = int(54.0 / 25.4 * DPI)      # 638 px

NAVY = (15, 42, 74)
BLUE = (29, 78, 216)
WHITE = (255, 255, 255)
GREY = (100, 116, 139)
LIGHT = (241, 245, 249)
BLACK = (15, 23, 42)

# ---------------------------------------------------------------------------
# Code 39 symbology.  Each character is nine elements, alternating bar/space;
# 'W' is a wide element, 'N' is narrow.
# ---------------------------------------------------------------------------
CODE39 = {
    "0": "NNNWWNWNN", "1": "WNNWNNNNW", "2": "NNWWNNNNW", "3": "WNWWNNNNN",
    "4": "NNNWWNNNW", "5": "WNNWWNNNN", "6": "NNWWWNNNN", "7": "NNNWNNWNW",
    "8": "WNNWNNWNN", "9": "NNWWNNWNN", "A": "WNNNNWNNW", "B": "NNWNNWNNW",
    "C": "WNWNNWNNN", "D": "NNNNWWNNW", "E": "WNNNWWNNN", "F": "NNWNWWNNN",
    "G": "NNNNNWWNW", "H": "WNNNNWWNN", "I": "NNWNNWWNN", "J": "NNNNWWWNN",
    "K": "WNNNNNNWW", "L": "NNWNNNNWW", "M": "WNWNNNNWN", "N": "NNNNWNNWW",
    "O": "WNNNWNNWN", "P": "NNWNWNNWN", "Q": "NNNNNNWWW", "R": "WNNNNNWWN",
    "S": "NNWNNNWWN", "T": "NNNNWNWWN", "U": "WWNNNNNNW", "V": "NWWNNNNNW",
    "W": "WWWNNNNNN", "X": "NWNNWNNNW", "Y": "WWNNWNNNN", "Z": "NWWNWNNNN",
    "-": "NWNNNNWNW", ".": "WWNNNNWNN", " ": "NWWNNNWNN", "*": "NWNNWNWNN",
}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a system font, degrading to Pillow's built-in if none is present."""
    candidates = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
                  else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_barcode(draw: ImageDraw.ImageDraw, text: str, x: int, y: int,
                  width: int, height: int) -> None:
    """Render ``text`` as a Code 39 barcode inside the given box."""
    payload = f"*{str(text).upper()}*"
    patterns = [CODE39[c] for c in payload if c in CODE39]
    if not patterns:
        return

    # Total narrow-unit width: each char is 6 narrow + 3 wide (=3x narrow),
    # plus a one-unit inter-character gap.
    narrow_units = len(patterns) * (6 + 3 * 3) + (len(patterns) - 1)
    unit = max(1, width // narrow_units)

    cursor = x
    for index, pattern in enumerate(patterns):
        for position, element in enumerate(pattern):
            element_width = unit * (3 if element == "W" else 1)
            if position % 2 == 0:          # even positions are bars
                draw.rectangle([cursor, y, cursor + element_width - 1, y + height],
                               fill=BLACK)
            cursor += element_width
        if index < len(patterns) - 1:
            cursor += unit                 # inter-character gap


def _circular_photo(image: Image.Image, size: int) -> Image.Image:
    """Square-crop and round the corners of a photograph."""
    image = image.convert("RGB")
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 3      # bias upward: heads sit high in photos
    image = image.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size, size], radius=size // 12, fill=255)
    output = Image.new("RGB", (size, size), WHITE)
    output.paste(image, (0, 0), mask)
    return output


def _placeholder_photo(size: int, initials: str) -> Image.Image:
    """Initials tile used when a student has no photograph on file."""
    image = Image.new("RGB", (size, size), LIGHT)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 12,
                           fill=(203, 213, 225))
    font = _font(int(size * 0.4), bold=True)
    box = draw.textbbox((0, 0), initials, font=font)
    draw.text(((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2 - box[1]),
              initials, font=font, fill=NAVY)
    return image


def generate_id_card(student: dict,
                     output_dir: Path | None = None) -> tuple[bool, str, Path | None]:
    """Render a single student's ID card as a PNG.

    Args:
        student: a row from ``v_student_full`` (or an equivalent dict).
    """
    output_dir = output_dir or ID_CARD_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        card = Image.new("RGB", (CARD_W, CARD_H), WHITE)
        draw = ImageDraw.Draw(card)

        # ---- header band -------------------------------------------------
        header_height = int(CARD_H * 0.235)
        draw.rectangle([0, 0, CARD_W, header_height], fill=NAVY)
        draw.rectangle([0, header_height, CARD_W, header_height + 6], fill=BLUE)

        logo_path = Path(config.get("logo_path", ""))
        text_left = 26
        if logo_path.exists():
            try:
                logo_size = header_height - 26
                logo = Image.open(logo_path).convert("RGBA").resize(
                    (logo_size, logo_size), Image.LANCZOS)
                backdrop = Image.new("RGB", (logo_size, logo_size), NAVY)
                backdrop.paste(logo, (0, 0), logo)
                card.paste(backdrop, (20, 13))
                text_left = 20 + logo_size + 16
            except Exception:                   # noqa: BLE001
                pass

        college_name = config.get("college_name", "College")
        name_font = _font(31 if len(college_name) < 34 else 25, bold=True)
        draw.text((text_left, 20), college_name, font=name_font, fill=WHITE)
        draw.text((text_left, 20 + (38 if len(college_name) < 34 else 32)),
                  config.get("college_address", "")[:62], font=_font(17), fill=(186, 205, 230))
        draw.text((text_left, 20 + (66 if len(college_name) < 34 else 58)),
                  f"Code: {config.get('college_code','')}   |   "
                  f"{config.get('college_phone','')}",
                  font=_font(16), fill=(186, 205, 230))

        # ---- photograph ---------------------------------------------------
        photo_size = int(CARD_H * 0.44)
        photo_x, photo_y = 30, header_height + 34

        photo_path = student.get("photo_path")
        photo = None
        if photo_path and Path(photo_path).exists():
            try:
                photo = _circular_photo(Image.open(photo_path), photo_size)
            except Exception:                   # noqa: BLE001
                photo = None
        if photo is None:
            initials = "".join(w[0] for w in str(student.get("full_name", "S")).split()[:2])
            photo = _placeholder_photo(photo_size, initials.upper() or "S")

        draw.rounded_rectangle(
            [photo_x - 4, photo_y - 4, photo_x + photo_size + 4, photo_y + photo_size + 4],
            radius=photo_size // 10, fill=BLUE)
        card.paste(photo, (photo_x, photo_y))

        # ---- details ------------------------------------------------------
        detail_x = photo_x + photo_size + 34
        cursor_y = header_height + 28

        draw.text((detail_x, cursor_y), str(student.get("full_name", "")).upper()[:26],
                  font=_font(33, bold=True), fill=NAVY)
        cursor_y += 44

        draw.text((detail_x, cursor_y), "STUDENT", font=_font(17, bold=True), fill=BLUE)
        cursor_y += 32

        fields = [
            ("Enrollment", student.get("enrollment_no", "")),
            ("Roll No", student.get("roll_no", "")),
            ("Branch", student.get("branch_name", "")),
            ("Semester", f"{student.get('semester_name','')}  "
                         f"{('Sec ' + str(student.get('section_name'))) if student.get('section_name') else ''}"),
            ("Batch", student.get("batch_name", "-")),
            ("Mobile", student.get("mobile", "-")),
        ]
        label_font, value_font = _font(17), _font(19, bold=True)
        for label, value in fields:
            draw.text((detail_x, cursor_y), f"{label}", font=label_font, fill=GREY)
            draw.text((detail_x + 130, cursor_y - 1), str(value or "-")[:30],
                      font=value_font, fill=BLACK)
            cursor_y += 28

        # ---- barcode ------------------------------------------------------
        # Sits above the footer rule with room for its caption; drawing it any
        # lower runs the enrollment number into the footer text.
        enrollment = str(student.get("enrollment_no", ""))
        barcode_h = 42
        barcode_y = CARD_H - 34 - 22 - barcode_h - 12   # rule, caption, bars, gap
        _draw_barcode(draw, enrollment, photo_x, barcode_y, int(CARD_W * 0.42), barcode_h)
        draw.text((photo_x, barcode_y + barcode_h + 4), enrollment,
                  font=_font(17, bold=True), fill=BLACK)

        # ---- footer -------------------------------------------------------
        draw.line([(0, CARD_H - 34), (CARD_W, CARD_H - 34)], fill=(203, 213, 225), width=2)
        draw.text((30, CARD_H - 26),
                  f"Session {config.get('current_academic_session','')}",
                  font=_font(15), fill=GREY)

        principal = config.get("principal_name", "Principal")
        draw.text((CARD_W - 30 - draw.textlength(principal, font=_font(16, bold=True)),
                   CARD_H - 74), principal, font=_font(16, bold=True), fill=NAVY)
        signature_label = "Principal"
        draw.line([(CARD_W - 210, CARD_H - 50), (CARD_W - 30, CARD_H - 50)],
                  fill=GREY, width=1)
        draw.text((CARD_W - 30 - draw.textlength(signature_label, font=_font(15)),
                   CARD_H - 26), signature_label, font=_font(15), fill=GREY)

        # ---- save ---------------------------------------------------------
        filename = safe_filename(
            f"IDCard_{enrollment}_{str(student.get('full_name','')).replace(' ', '_')}") + ".png"
        path = output_dir / filename
        card.save(path, "PNG", dpi=(DPI, DPI))

        return True, f"ID card generated: {filename}", path

    except Exception as exc:                    # noqa: BLE001
        logger.error("ID card generation failed for %s: %s",
                     student.get("enrollment_no"), exc, exc_info=True)
        return False, f"Could not generate the ID card: {exc}", None


def generate_bulk(students: list, on_progress=None) -> tuple[bool, str, list[Path]]:
    """Generate cards for many students, reporting progress as it goes."""
    generated: list[Path] = []
    failed = 0

    for index, student in enumerate(students, start=1):
        ok, _, path = generate_id_card(dict(student))
        if ok and path:
            generated.append(path)
        else:
            failed += 1
        if on_progress:
            on_progress(index, len(students), student.get("full_name", ""))

    message = f"{len(generated)} ID card(s) generated in {ID_CARD_DIR}."
    if failed:
        message += f" {failed} failed."
    return bool(generated), message, generated


def generate_print_sheet(card_paths: list[Path],
                         output_path: Path | None = None) -> tuple[bool, str, Path | None]:
    """Lay cards out 2 x 5 on an A4 sheet for batch printing."""
    if not card_paths:
        return False, "No ID cards to lay out.", None

    A4_W, A4_H = int(210 / 25.4 * DPI), int(297 / 25.4 * DPI)
    columns, rows_per_page = 2, 5
    margin_x, margin_y, gap = 90, 100, 40

    output_path = output_path or (ID_CARD_DIR /
                                  f"IDCard_PrintSheet_{datetime.now():%Y%m%d_%H%M%S}.pdf")
    pages: list[Image.Image] = []

    try:
        for start in range(0, len(card_paths), columns * rows_per_page):
            page = Image.new("RGB", (A4_W, A4_H), WHITE)
            chunk = card_paths[start:start + columns * rows_per_page]

            for index, card_path in enumerate(chunk):
                card = Image.open(card_path)
                x = margin_x + (index % columns) * (CARD_W + gap)
                y = margin_y + (index // columns) * (CARD_H + gap)
                page.paste(card, (x, y))
                # Cut guides.
                ImageDraw.Draw(page).rectangle(
                    [x - 1, y - 1, x + CARD_W, y + CARD_H],
                    outline=(203, 213, 225), width=1)

            draw = ImageDraw.Draw(page)
            draw.text((margin_x, 46), config.get("college_name", ""),
                      font=_font(30, bold=True), fill=NAVY)
            draw.text((margin_x, A4_H - 60),
                      f"Generated {datetime.now():%d %b %Y %I:%M %p} - "
                      f"{len(card_paths)} card(s)", font=_font(20), fill=GREY)
            pages.append(page)

        pages[0].save(output_path, "PDF", resolution=DPI,
                      save_all=True, append_images=pages[1:])
        return True, f"Print sheet saved: {output_path.name} ({len(pages)} page(s))", output_path

    except Exception as exc:                    # noqa: BLE001
        logger.error("Print sheet failed: %s", exc, exc_info=True)
        return False, f"Could not build the print sheet: {exc}", None
