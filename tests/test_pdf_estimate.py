"""PDF-кошторис: генератор і ендпоінт.

Найважливіше тут — тест на кирилицю. Вбудовані шрифти reportlab її не
знають, і якщо папка assets/fonts не потрапить у деплой, документ
згенерується успішно, але замість тексту будуть порожні гліфи. Помилки
не буде — буде мовчазний брак, який помітить клієнт.
"""
import io
import os
import asyncio

import pytest

from pdf_estimate import build_estimate_pdf, _money, _ensure_fonts

ORDER = {"name": "Олена Ткаченко", "phone": "+380671234567",
         "type": "Квартира", "address": "вул. Хрещатик 1 · 78 м²",
         "date": "18.07.2026"}
BUDGET = {"work": 93970, "mat_min": 92950, "mat_max": 128400,
          "general_lines": [{"label": "Демонтаж", "qty": 78, "unit": "м²",
                             "work": 15600, "mat_min": 0}]}
ROOMS = [{"name": "Вітальня", "area": 26, "work": 41200, "mat_min": 38900,
          "lines": [{"label": "Ламінат", "qty": 26, "unit": "м²",
                     "work": 10530, "mat_min": 15600}]}]


def _text(pdf_bytes):
    pdfplumber = pytest.importorskip("pdfplumber")
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def test_fonts_are_bundled():
    """Шрифти мусять лежати в репозиторії, а не братися із системи —
    на Render кириличних шрифтів може не бути взагалі."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("DejaVuSans-subset.ttf", "DejaVuSans-Bold-subset.ttf"):
        path = os.path.join(root, "assets", "fonts", name)
        assert os.path.exists(path), f"немає {path}"
        assert os.path.getsize(path) > 5000, f"{name} підозріло малий"
    assert _ensure_fonts() is True


def test_pdf_is_valid_and_not_empty():
    pdf = build_estimate_pdf(ORDER, BUDGET, ROOMS)
    assert pdf[:5] == b"%PDF-", "це не PDF"
    assert len(pdf) > 3000


def test_cyrillic_survives_round_trip():
    """Текст мусить читатись назад — саме тут ловиться відсутній шрифт."""
    text = _text(build_estimate_pdf(ORDER, BUDGET, ROOMS))
    for expected in ("Кошторис ремонту", "Олена Ткаченко", "Вітальня", "Ламінат"):
        assert expected in text, f"у PDF немає «{expected}»"
    assert "\x00" not in text, "у документі є порожні гліфи — бракує символів у шрифті"


def test_ukrainian_apostrophe_renders():
    """Обʼєкт — апостроф U+02BC. Його легко забути при урізанні шрифту."""
    assert "Обʼєкт" in _text(build_estimate_pdf(ORDER, BUDGET, ROOMS))


def test_totals_are_shown():
    text = _text(build_estimate_pdf(ORDER, BUDGET, ROOMS))
    assert "186" in text and "222" in text, "немає підсумкової суми"


def test_branding_is_applied():
    pdf = build_estimate_pdf(ORDER, BUDGET, ROOMS, branding={
        "company": "Будівельна артіль", "phone": "+380 44 111 22 33",
        "site": "artil.example", "accent": "#c0392b"})
    text = _text(pdf)
    assert "Будівельна артіль" in text
    assert "artil.example" in text


def test_broken_accent_color_does_not_crash():
    """Колір із налаштувань тенанта може бути будь-яким — документ важливіший."""
    pdf = build_estimate_pdf(ORDER, BUDGET, ROOMS, branding={"accent": "не-колір"})
    assert pdf[:5] == b"%PDF-"


def test_survives_empty_rooms_and_lines():
    pdf = build_estimate_pdf(ORDER, {"work": 0, "mat_min": 0, "mat_max": 0}, [])
    assert pdf[:5] == b"%PDF-"


def test_survives_missing_order_fields():
    pdf = build_estimate_pdf({}, BUDGET, ROOMS)
    assert pdf[:5] == b"%PDF-"


@pytest.mark.parametrize("value,expected", [
    (1234567, "1\u00a0234\u00a0567"), (0, "0"), (999, "999"),
    (1234.6, "1\u00a0235"), (None, "0"), ("абв", "0"),
])
def test_money_formatting(value, expected):
    assert _money(value) == expected


# ── Ендпоінт ─────────────────────────────────────────────
pg_only = pytest.mark.skipif(not os.getenv("DATABASE_URL"),
                             reason="потрібен DATABASE_URL")


@pg_only
def test_endpoint_requires_auth():
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    import main

    async def go():
        app = web.Application()
        app.router.add_get('/api/order_pdf', main.api_order_pdf)
        async with TestClient(TestServer(app)) as cli:
            return (await cli.get('/api/order_pdf?row=1')).status

    assert asyncio.run(go()) == 401


@pg_only
def test_endpoint_rejects_bad_row():
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    import main

    async def go():
        app = web.Application()
        app.router.add_get('/api/order_pdf', main.api_order_pdf)
        original = main.auth_request
        main.auth_request = lambda request: ("1", "admin")
        try:
            async with TestClient(TestServer(app)) as cli:
                bad = (await cli.get('/api/order_pdf?row=абв')).status
                missing = (await cli.get('/api/order_pdf?row=99999999')).status
                return bad, missing
        finally:
            main.auth_request = original

    assert asyncio.run(go()) == (400, 404)
