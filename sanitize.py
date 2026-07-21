"""Очищення HTML, який згенерувала мовна модель.

НАВІЩО. Звіт від Gemini вставляється в кабінет через dangerouslySetInnerHTML —
тобто браузер виконає все, що там є. Модель не зловмисна, але вона переказує
ТЕКСТ, ЯКИЙ УВІВ КОРИСТУВАЧ: ім'я клієнта, адресу, коментарі до кімнат. Тобто
гість із сайту може написати в полі «побажання» шматок <script>, модель
перекаже його в звіті — і скрипт виконається вже в браузері менеджера, з його
сесійним токеном у localStorage. Це класичний збережений XSS через третю особу.

Тому: пропускаємо лише розмітку тексту, все інше вирізаємо. Жодних атрибутів
(крім colspan/rowspan у таблицях), жодних посилань, скриптів і стилів.
Парсер стандартний (html.parser) — нових залежностей не додаємо.
"""
from html import escape
from html.parser import HTMLParser

# Теги, які потрібні звіту й не можуть нічого виконати.
ALLOWED_TAGS = {
    "p", "br", "hr", "b", "strong", "i", "em", "u", "s",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "div", "span", "small", "blockquote", "pre", "code",
}
VOID_TAGS = {"br", "hr"}
ALLOWED_ATTRS = {"colspan", "rowspan"}          # лише в клітинках таблиці

# Теги, вміст яких треба викинути ЦІЛКОМ, а не лише саму обгортку:
# інакше з <script>alert(1)</script> лишився б голий текст alert(1),
# а зі <style> — купа css у тілі звіту.
DROP_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "math"}


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.open_stack = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS:
            return
        kept = ""
        if tag in ("td", "th"):
            for name, value in attrs:
                if name.lower() in ALLOWED_ATTRS and str(value).isdigit():
                    kept += f' {name.lower()}="{value}"'
        if tag in VOID_TAGS:
            self.out.append(f"<{tag}>")
        else:
            self.out.append(f"<{tag}{kept}>")
            self.open_stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag in self.open_stack:
            # Закриваємо все, що модель забула закрити всередині.
            while self.open_stack:
                last = self.open_stack.pop()
                self.out.append(f"</{last}>")
                if last == tag:
                    break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self.skip_depth:
            return
        self.out.append(escape(data))

    def result(self):
        while self.open_stack:
            self.out.append(f"</{self.open_stack.pop()}>")
        return "".join(self.out)


def sanitize_report_html(raw: str) -> str:
    """Повертає безпечний HTML. На будь-якій помилці — екранований текст.

    Краще показати менеджеру звіт без розмітки, ніж не показати нічого
    або показати те, що виконається.
    """
    if not raw:
        return ""
    try:
        cleaner = _Cleaner()
        cleaner.feed(str(raw))
        cleaner.close()
        return cleaner.result()
    except Exception:
        return escape(str(raw))
