"""PDF-кошторис для клієнта.

НАВІЩО. Зараз менеджер після анкети зводить кошторис руками — копіює числа
в документ, форматує, надсилає. Це години роботи на тиждень і джерело
помилок. Тут той самий кошторис, який рахує калькулятор, виходить готовим
документом за секунду.

ПРО ШРИФТИ. Вбудовані шрифти reportlab кирилиці не знають — замість тексту
вийдуть чорні прямокутники. Тому в репозиторії лежать урізані DejaVu
(assets/fonts): з них залишені лише українські, латинські літери, цифри й
пунктуація, тож два файли важать 39 КБ замість 1.4 МБ. Покладатися на
шрифти системи не можна: на Render їх може не бути.

ПРО БРЕНДИНГ. Назва компанії, контакти й акцентний колір беруться з
налаштувань тенанта (tenants.settings). На Етапі B кожна компанія матиме
свої; поки що діють значення за замовчуванням.
"""
import io
import os
import logging
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, KeepTogether)

logger = logging.getLogger(__name__)

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
FONT = "DejaVu"
FONT_BOLD = "DejaVu-Bold"
_FONTS_READY = None

DEFAULT_BRANDING = {
    "company": "Ремонтник UA",
    "phone": "",
    "site": "",
    "accent": "#0a84ff",
    "note": "Кошторис попередній. Остаточна вартість визначається після заміру на обʼєкті.",
}


def _ensure_fonts():
    """Реєструє шрифти один раз на процес.

    Якщо файлів немає — не падаємо: документ усе одно згенерується, але
    кирилиця перетвориться на прямокутники, тож пишемо гучну помилку.
    """
    global _FONTS_READY
    if _FONTS_READY is not None:
        return _FONTS_READY
    try:
        pdfmetrics.registerFont(TTFont(FONT, os.path.join(_FONTS_DIR, "DejaVuSans-subset.ttf")))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, os.path.join(_FONTS_DIR, "DejaVuSans-Bold-subset.ttf")))
        pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD)
        _FONTS_READY = True
    except Exception as e:
        logger.error("Не вдалося зареєструвати шрифти для PDF (%s). "
                     "Кирилиця в документі буде нечитабельною. "
                     "Перевір, що assets/fonts/ потрапила в репозиторій.", e)
        _FONTS_READY = False
    return _FONTS_READY


def _money(value):
    """12345.6 -> «12 345». Нерозривний пробіл, щоб число не рвалося."""
    try:
        return f"{round(float(value)):,}".replace(",", "\u00a0")
    except (TypeError, ValueError):
        return "0"


def _human_date(value):
    """«2026-07-19 11:07» -> «19.07.2026».

    У базі дата лежить у тому вигляді, у якому її записав бот, і формат
    за роки встиг помінятись. Клієнтові ж потрібен звичайний день —
    без годин і без ISO. Незнайомий формат віддаємо як є: краще показати
    щось дивне, ніж загубити дату зовсім.
    """
    text = str(value or "").strip()
    if not text:
        return datetime.now().strftime("%d.%m.%Y")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%d.%m.%Y %H:%M", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return text


def _styles(accent):
    base = FONT if _FONTS_READY else "Helvetica"
    bold = FONT_BOLD if _FONTS_READY else "Helvetica-Bold"
    return {
        "base": base, "bold": bold,
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=19, leading=23, textColor=accent),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=12, leading=15,
                             textColor=colors.HexColor("#222222"), spaceBefore=4, spaceAfter=6),
        "p": ParagraphStyle("p", fontName=base, fontSize=9.5, leading=13),
        "small": ParagraphStyle("small", fontName=base, fontSize=8, leading=11,
                                textColor=colors.HexColor("#666666")),
        "cell": ParagraphStyle("cell", fontName=base, fontSize=8.5, leading=11),
        "cell_b": ParagraphStyle("cell_b", fontName=bold, fontSize=8.5, leading=11),
        "num": ParagraphStyle("num", fontName=base, fontSize=8.5, leading=11, alignment=TA_RIGHT),
    }


def _lines_table(lines, st, width):
    """Таблиця позицій. Порожній список -> None, щоб не малювати шапку ні над чим."""
    if not lines:
        return None
    head = [Paragraph(t, st["cell_b"]) for t in ("Найменування", "К-сть", "Робота", "Матеріал")]
    rows = [head]
    for ln in lines:
        qty = ln.get("qty")
        unit = ln.get("unit") or ""
        rows.append([
            Paragraph(str(ln.get("label") or ln.get("key") or ""), st["cell"]),
            Paragraph(f"{qty:g}\u00a0{unit}".strip() if qty is not None else "", st["num"]),
            Paragraph(_money(ln.get("work")), st["num"]),
            Paragraph(_money(ln.get("mat_min")), st["num"]),
        ])
    t = Table(rows, colWidths=[width * 0.49, width * 0.15, width * 0.18, width * 0.18], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f4f7")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#d7dbe0")),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, colors.HexColor("#eceff2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def build_estimate_pdf(order, budget, rooms=None, branding=None):
    """Збирає PDF і повертає його байтами.

    order   — картка заявки (_row_meta): name, phone, object_type, address, date_text
    budget  — {work, mat_min, mat_max, total, general_lines}
    rooms   — [{name, area, work, mat_min, lines}]
    """
    _ensure_fonts()
    b = {**DEFAULT_BRANDING, **(branding or {})}
    try:
        accent = colors.HexColor(b["accent"])
    except Exception:
        accent = colors.HexColor(DEFAULT_BRANDING["accent"])
    st = _styles(accent)

    buf = io.BytesIO()
    margin = 16 * mm
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=margin, rightMargin=margin, topMargin=14 * mm, bottomMargin=16 * mm,
        title=f"Кошторис — {order.get('name') or 'обʼєкт'}",
        author=b["company"],
    )
    width = doc.width
    story = []

    # ── Шапка ────────────────────────────────────────────
    contacts = "  ·  ".join(x for x in (b.get("phone"), b.get("site")) if x)
    story.append(Table(
        [[Paragraph(b["company"], st["h1"]),
          Paragraph(contacts, ParagraphStyle("r", parent=st["small"], alignment=TA_RIGHT))]],
        colWidths=[width * 0.6, width * 0.4],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                          ("LEFTPADDING", (0, 0), (-1, -1), 0),
                          ("RIGHTPADDING", (0, 0), (-1, -1), 0)])))
    story.append(Spacer(1, 4))
    story.append(Table([[""]], colWidths=[width], rowHeights=[2],
                       style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)])))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Кошторис ремонту", st["h2"]))

    # ── Про обʼєкт ───────────────────────────────────────
    # У картці заявки поля називаються type/date (див. _row_meta), а не
    # object_type/date_text — приймаємо обидва варіанти, щоб генератор не
    # залежав від того, звідки прийшов словник.
    info = [("Замовник", order.get("name")),
            ("Телефон", order.get("phone")),
            ("Обʼєкт", order.get("type") or order.get("object_type")),
            ("Адреса", order.get("address")),
            ("Дата", _human_date(order.get("date") or order.get("date_text")))]
    info_rows = [[Paragraph(k, st["small"]), Paragraph(str(v), st["p"])]
                 for k, v in info if v]
    if info_rows:
        story.append(Table(info_rows, colWidths=[width * 0.18, width * 0.82],
                           style=TableStyle([
                               ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])))
    story.append(Spacer(1, 14))

    # ── Підсумок ─────────────────────────────────────────
    # Головне число документа — воно має читатись першим, до деталізації.
    work, mat_min, mat_max = budget.get("work", 0), budget.get("mat_min", 0), budget.get("mat_max", 0)
    total_min, total_max = work + mat_min, work + mat_max
    summary = [
        [Paragraph("Роботи", st["cell"]), Paragraph(_money(work) + " грн", st["num"])],
        [Paragraph("Матеріали", st["cell"]),
         Paragraph(f"{_money(mat_min)} – {_money(mat_max)} грн" if mat_max > mat_min
                   else _money(mat_min) + " грн", st["num"])],
        [Paragraph("Разом", st["cell_b"]),
         Paragraph(f"<b>{_money(total_min)} – {_money(total_max)} грн</b>" if total_max > total_min
                   else f"<b>{_money(total_min)} грн</b>",
                   ParagraphStyle("tot", parent=st["num"], fontName=st["bold"], fontSize=11))],
    ]
    t = Table(summary, colWidths=[width * 0.6, width * 0.4])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f9fb")),
        ("LINEABOVE", (0, 2), (-1, 2), 0.6, colors.HexColor("#d7dbe0")),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    # ── По приміщеннях ───────────────────────────────────
    for r in (rooms or []):
        title = r.get("name") or "Приміщення"
        area = r.get("area")
        head = f"{title}" + (f" · {area} м²" if area else "")
        block = [Paragraph(head, st["h2"]),
                 Paragraph(f"Роботи {_money(r.get('work'))} грн · "
                           f"матеріали {_money(r.get('mat_min'))} грн", st["small"]),
                 Spacer(1, 5)]
        lt = _lines_table(r.get("lines"), st, width)
        if lt:
            block.append(lt)
        block.append(Spacer(1, 12))
        # KeepTogether не дає заголовку кімнати відірватись від її таблиці
        story.append(KeepTogether(block))

    general = budget.get("general_lines") or []
    if general:
        story.append(KeepTogether([
            Paragraph("Загальні роботи", st["h2"]),
            Paragraph("Демонтаж, стяжка, стелі, двері, електророзводка та інше, "
                      "що не належить до конкретної кімнати.", st["small"]),
            Spacer(1, 5),
            _lines_table(general, st, width),
        ]))
        story.append(Spacer(1, 12))

    if b.get("note"):
        story.append(Spacer(1, 4))
        story.append(Paragraph(b["note"], st["small"]))

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont(st["base"], 7.5)
        canvas.setFillColor(colors.HexColor("#8a9099"))
        canvas.drawString(margin, 9 * mm,
                          f"{b['company']} · {datetime.now().strftime('%d.%m.%Y')}")
        canvas.drawRightString(A4[0] - margin, 9 * mm, f"стор. {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()