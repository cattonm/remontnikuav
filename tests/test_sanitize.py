"""Очищення HTML, який згенерувала мовна модель.

Звіт вставляється в кабінет через dangerouslySetInnerHTML, а модель
переказує текст, введений КЛІЄНТОМ. Тобто гість із сайту може підкинути
розмітку в поле «побажання», модель перекаже її в звіті — і вона
виконається в браузері менеджера. Ці тести фіксують, що не виконається.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sanitize import sanitize_report_html


def test_keeps_normal_markup():
    html = "<h2>Кошторис</h2><p>Стіни: <b>фарбування</b></p><ul><li>Ванна</li></ul>"
    out = sanitize_report_html(html)
    assert "<h2>" in out and "<b>" in out and "<li>" in out
    assert "Кошторис" in out


def test_drops_script_with_content():
    out = sanitize_report_html("<p>Привіт</p><script>alert(document.cookie)</script>")
    assert "<script" not in out.lower()
    # Вміст скрипта теж має зникнути, а не лишитись голим текстом
    assert "alert" not in out
    assert "Привіт" in out


def test_drops_event_handlers():
    out = sanitize_report_html('<div onclick="steal()">текст</div>')
    assert "onclick" not in out.lower()
    assert "steal" not in out
    assert "текст" in out


def test_drops_links_and_images():
    out = sanitize_report_html('<a href="javascript:evil()">клік</a><img src=x onerror=evil()>')
    assert "javascript" not in out.lower()
    assert "<a" not in out.lower()
    assert "<img" not in out.lower()
    assert "клік" in out


def test_drops_iframe_and_style():
    out = sanitize_report_html('<iframe src="//evil"></iframe><style>body{display:none}</style><p>ок</p>')
    assert "iframe" not in out.lower()
    assert "display:none" not in out
    assert "ок" in out


def test_escapes_bare_angle_brackets():
    out = sanitize_report_html("Площа < 20 м² та > 10 м²")
    assert "&lt;" in out and "&gt;" in out


def test_keeps_table_spans_only():
    out = sanitize_report_html('<table><tr><td colspan="2" style="color:red">A</td></tr></table>')
    assert 'colspan="2"' in out
    assert "style" not in out


def test_closes_unclosed_tags():
    out = sanitize_report_html("<p>перший<p>другий")
    assert out.count("</p>") == 2


def test_empty_and_none_safe():
    assert sanitize_report_html("") == ""
    assert sanitize_report_html(None) == ""


def test_plain_text_survives():
    assert "Ремонт квартири" in sanitize_report_html("Ремонт квартири")
