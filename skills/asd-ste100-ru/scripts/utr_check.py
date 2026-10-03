#!/usr/bin/env python3
"""Проверка русского текста по разделу 8.2 ГОСТ Р 58049—2017 (УТР).

Скрипт проверяет строение текста: длину предложений, абзацы, списки, ссылки.
Словарей у скрипта нет. Жаргон, метафоры и синонимы проверяет агент.

Запуск:
    python3 utr_check.py ФАЙЛ [ФАЙЛ ...]
    python3 utr_check.py - < текст.md
    python3 utr_check.py --json ФАЙЛ
    python3 utr_check.py --selftest

Код выхода 1 означает, что есть нарушения. Советы на код выхода не влияют.
Глаголы раздела 6 ГОСТа («должно», «необходимо», «следует», «может») скрипт не проверяет.
"""
import json
import re
import sys

MAX_WORDS = 20      # 8.2.5.1, 8.2.6.1
MAX_SENTENCES = 6   # 8.2.6.2

VIOLATION = "нарушение"
ADVICE = "совет"

FENCE = re.compile(r"^\s*(```|~~~)")
LIST_ITEM = re.compile(r"^(?P<indent>\s*)(?P<marker>[-*+]|\d+[.)])\s+(?P<body>.*)$")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<body>.*)$")
BLOCKQUOTE = re.compile(r"^\s*>\s?")
TABLE_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
HTML_COMMENT = re.compile(r"<!--.*?-->")

INLINE_CODE = re.compile(r"`[^`]*`")
LINK = re.compile(r"!?\[(?P<text>[^\]]*)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")
QUOTED = re.compile(r"«[^«»]*»|„[^„“]*“|\"[^\"]*\"")
EMPHASIS = re.compile(r"\*+|(?<!\w)_+|_+(?!\w)")
BRACKETS = re.compile(r"\([^()]*\)")
SENTENCE_END = re.compile(r"[.!?…]+[\"»”’)]*\s+")
WORD = re.compile(r"\w")

# Подстановки считаются одним словом (8.2.5.1): код, путь, команда, цитата.
CODE_TOKEN = " КОД "
URL_TOKEN = " АДРЕС "
QUOTE_TOKEN = " ЦИТАТА "

LINK_WORDS = {"здесь", "тут", "сюда", "ссылка", "ссылке", "ссылку", "по ссылке",
              "на ссылку", "эта ссылка", "этой ссылке"}
DANGLING = re.compile(r"(?:^|\s)(и|или)\s*$", re.I)

ENDING = r"(?:ий|ый|ой|ая|яя|ое|ее|ие|ые|его|ого|ей|ему|ому|им|ым|ими|ыми|их|ых|ую|юю|ем|ом)"
PARTICIPLE = re.compile(
    r",\s+(?P<m>[а-яё]+(?:ющ|ящ|ущ|ащ|вш|ем|им|енн|ённ|анн|янн)" + ENDING + r"(?:ся|сь)?)\b",
    re.I)
GERUND = re.compile(
    r"(?:^|[,.!?:;]\s+)(?P<m>[а-яё]{3,}(?:вшись|ясь|ывая|ивая|уя|юя|ив|ав|ыв))\b", re.I)
PASSIVE_SHORT = re.compile(
    r"\b(?P<m>(?:был|была|было|были|будет|будут)\s+[а-яё]+"
    r"(?:ен|ён|ена|ено|ены|ан|ана|ано|аны|ян|яна|яно|яны|ыт|ыта|ыто|ыты|ут|ута|уто|уты))\b",
    re.I)
PASSIVE_REFLEXIVE = re.compile(
    r"\b(?P<m>(?P<verb>[а-яё]+(?:ется|ются|ится|ятся))\s+[а-яё]+(?:ом|ем|ём|ой|ей|ами|ями))\b",
    re.I)
# Связки с творительным падежом — не страдательный залог: «является основой».
LINKING_VERBS = ("явля", "называ", "счита", "станов", "оказыва")
NOMINAL = re.compile(
    r"\b(?P<m>осуществл[а-яё]*\s+(?:[а-яё]+" + ENDING + r"\s+)?[а-яё]+"
    r"|(?:производ|произвед|произвёл|произвел|провод|провед|провёл|провел|выполн)[а-яё]*\s+"
    r"(?:[а-яё]+" + ENDING + r"\s+)?"
    r"[а-яё]+(?:ние|ния|нию|нием|нии|тие|тия|тию|ция|ции|цию|цией|ка|ку|ки|кой))\b",
    re.I)


def prepare(line):
    """Заменить код, ссылки, адреса и цитаты на одно слово."""
    line = HTML_COMMENT.sub(" ", line)
    line = INLINE_CODE.sub(CODE_TOKEN, line)
    line = LINK.sub(lambda m: " " + m.group("text") + " ", line)
    line = URL.sub(URL_TOKEN, line)
    line = QUOTED.sub(QUOTE_TOKEN, line)
    return EMPHASIS.sub("", line)


def count_words(text):
    return sum(1 for token in text.split() if WORD.search(token))


def split_sentences(text):
    """Вернуть пары (смещение, предложение)."""
    sentences, start = [], 0
    for m in SENTENCE_END.finditer(text):
        rest = text[m.end():].lstrip("«\"„([")
        if not rest or not rest[0].isupper():
            continue
        before = text[start:m.start()].split()
        last = before[-1].strip("(«\"") if before else ""
        if len(last) == 1 and last.isalpha():
            continue  # инициалы и сокращения из одной буквы: «Н. Е.», «г.», «п.»
        sentence = text[start:m.end()].strip()
        if sentence:
            sentences.append((start, sentence))
        start = m.end()
    tail = text[start:].strip()
    if tail:
        sentences.append((start, tail))
    return sentences


class Unit:
    """Абзац, пункт списка, заголовок или ячейка таблицы."""

    def __init__(self, kind, lines):
        self.kind = kind
        self.lines = lines  # [(номер строки, исходный текст)]


def split_table_row(line):
    stripped = line.strip()
    if "|" not in stripped:
        return None
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]
    return cells if len(cells) >= 2 else None


def parse(text):
    """Разбить Markdown на единицы проверки. Код и служебные блоки пропустить."""
    lines = text.splitlines()
    units, paragraph = [], []
    in_fence = False
    index = 0

    def close_paragraph():
        if paragraph:
            units.append(Unit("абзац", list(paragraph)))
            paragraph.clear()

    if lines and lines[0].strip() == "---":
        for end in range(1, len(lines)):
            if lines[end].strip() == "---":
                index = end + 1
                break

    while index < len(lines):
        lineno, line = index + 1, lines[index]
        if FENCE.match(line):
            close_paragraph()
            in_fence = not in_fence
            index += 1
            continue
        if in_fence or not line.strip():
            close_paragraph()
            index += 1
            continue
        line = BLOCKQUOTE.sub("", line)
        cells = split_table_row(line)
        next_cells = split_table_row(lines[index + 1]) if index + 1 < len(lines) else None
        if cells and next_cells and all(TABLE_SEPARATOR_CELL.match(c) for c in next_cells):
            close_paragraph()
            units.extend(Unit("ячейка", [(lineno, c)]) for c in cells)
            index += 2
            while index < len(lines):
                row = split_table_row(lines[index])
                if not row:
                    break
                units.extend(Unit("ячейка", [(index + 1, c)]) for c in row)
                index += 1
            continue
        heading = HEADING.match(line)
        if heading:
            close_paragraph()
            units.append(Unit("заголовок", [(lineno, heading.group("body"))]))
            index += 1
            continue
        item = LIST_ITEM.match(line)
        if item:
            close_paragraph()
            body = [(lineno, item.group("body"))]
            content_indent = len(item.group("indent")) + len(item.group("marker")) + 1
            index += 1
            while index < len(lines):
                nxt = lines[index]
                if (not nxt.strip() or LIST_ITEM.match(nxt) or FENCE.match(nxt)
                        or len(nxt) - len(nxt.lstrip()) < content_indent):
                    break
                body.append((index + 1, nxt.strip()))
                index += 1
            units.append(Unit("пункт", body))
            continue
        paragraph.append((lineno, line.strip()))
        index += 1
    close_paragraph()
    return units


def finding(filename, line, level, rule, message, clause, match):
    return {"file": filename, "line": line, "level": level, "rule": rule,
            "message": message, "clause": clause, "match": match}


def check_unit(unit, filename):
    findings = []
    starts, joined = [], ""
    for lineno, raw in unit.lines:
        for link in LINK.finditer(INLINE_CODE.sub(" ", raw)):
            text = link.group("text").strip()
            if text.lower() in LINK_WORDS:
                findings.append(finding(
                    filename, lineno, VIOLATION, "текст-ссылки",
                    f"Текст ссылки «{text}». Назовите в тексте ссылки цель.",
                    "8.2.3.18", text))
        starts.append((len(joined), lineno))
        joined += prepare(raw) + " "

    def line_at(offset):
        line = starts[0][1]
        for start, lineno in starts:
            if start > offset:
                break
            line = lineno
        return line

    bracketed = []

    def blank(m):
        bracketed.append((m.start(), m.group(0)[1:-1]))
        return " " * len(m.group(0))

    for _ in range(3):  # вложенные скобки снимаются за несколько проходов
        joined, replaced = BRACKETS.subn(blank, joined)
        if not replaced:
            break

    sentences = split_sentences(joined)
    for offset, sentence in sentences + bracketed:
        words = count_words(sentence)
        if words > MAX_WORDS:
            findings.append(finding(
                filename, line_at(offset), VIOLATION, "длина",
                f"{words} слов, больше {MAX_WORDS}. Разделите предложение.",
                "8.2.5.1, 8.2.6.1", f"{words} слов"))

    if unit.kind == "абзац" and len(sentences) > MAX_SENTENCES:
        findings.append(finding(
            filename, unit.lines[0][0], VIOLATION, "абзац",
            f"{len(sentences)} предложений, больше {MAX_SENTENCES}. Разделите абзац по темам.",
            "8.2.6.2", f"{len(sentences)} предложений"))

    if unit.kind == "пункт":
        dangling = DANGLING.search(joined.rstrip())
        if dangling:
            findings.append(finding(
                filename, unit.lines[-1][0], VIOLATION, "пункт-списка",
                f"Пункт оборван на «{dangling.group(1)}». "
                "Допишите пункт или объедините его со следующим.",
                "8.2.3.16", dangling.group(1)))

    advice = [
        (PARTICIPLE, "причастие", "Причастный оборот «{}». Замените придаточным "
         "или отдельным предложением, если смысл сохранится.", "8.2.3.11"),
        (GERUND, "деепричастие", "Деепричастный оборот «{}». Замените отдельным "
         "предложением, если смысл сохранится.", "8.2.3.11"),
        (PASSIVE_SHORT, "залог", "Возможен страдательный залог: «{}». "
         "Замените действительным залогом или неопределённо-личным оборотом.", "8.2.3.13"),
        (PASSIVE_REFLEXIVE, "залог", "Возможен страдательный залог: «{}». "
         "Замените действительным залогом или неопределённо-личным оборотом.", "8.2.3.13"),
        (NOMINAL, "отглагольное", "«{}»: используйте глагол вместо "
         "отглагольного существительного.", "8.2.3.10"),
    ]
    for pattern, rule, message, clause in advice:
        for m in pattern.finditer(joined):
            if "verb" in pattern.groupindex and m.group("verb").lower().startswith(LINKING_VERBS):
                continue
            match = " ".join(m.group("m").split())
            findings.append(finding(filename, line_at(m.start("m")), ADVICE, rule,
                                    message.format(match), clause, match))
    return findings


def check(text, filename="-"):
    findings = []
    for unit in parse(text):
        findings.extend(check_unit(unit, filename))
    findings.sort(key=lambda f: (f["line"], f["level"] != VIOLATION, f["rule"]))
    return findings


def report(findings, as_json):
    violations = sum(1 for f in findings if f["level"] == VIOLATION)
    advice = len(findings) - violations
    if as_json:
        print(json.dumps({"findings": findings, "violations": violations,
                          "advice": advice}, ensure_ascii=False, indent=2))
        return violations
    for f in findings:
        prefix = "" if f["level"] == VIOLATION else "совет: "
        print(f"{f['file']}:{f['line']} {prefix}{f['rule']}: {f['message']} ({f['clause']})")
    print(f"Нарушений: {violations}. Советов: {advice}.")
    return violations


def selftest():
    """Проверки на фразах из раздела 8.2 ГОСТ Р 58049—2017 и на счёте слов."""
    def rules(text):
        return [f["rule"] for f in check(text)]

    twenty = " ".join(["слово"] * 20) + "."
    assert rules(twenty) == [], rules(twenty)
    assert rules(" ".join(["слово"] * 21) + ".") == ["длина"]

    # 8.2.5.1: число, буквенно-цифровой идентификатор, аббревиатура и цитата — одно слово.
    # Код в обратных кавычках — тоже одно слово (адаптация навыка).
    one_word = " ".join(["слово"] * 15) + " `код из трёх слов` «цитата из трёх слов» 15-СЦС-45 ТОиР 1000."
    assert rules(one_word) == [], check(one_word)

    # 8.2.5.1: текст в скобках считается новым предложением.
    bracket = " ".join(["слово"] * 15) + " (" + " ".join(["слово"] * 10) + ")."
    assert rules(bracket) == [], check(bracket)
    assert rules("Слово (" + " ".join(["слово"] * 21) + ").") == ["длина"]

    # Сокращения «т. е.», «т. п.» и инициалы не делят предложение.
    terms = ("Использование терминологии должно быть единообразным, т. е. одни и те же термины "
             "должны быть использованы для описания одних и тех же объектов во всем тексте.")
    assert len(split_sentences(prepare(terms))) == 1
    developer = "Разработчик — Институт имени Н. Е. Жуковского. Стандарт введен впервые."
    assert len(split_sentences(prepare(developer))) == 2

    # 8.2.6.2: не больше шести предложений в абзаце.
    six = " ".join(["Слово."] * 6)
    assert rules(six) == []
    assert rules(six + " Слово.") == ["абзац"]

    # 8.2.3.16: пункт списка не обрывается на союзе.
    assert rules("- для организации перекрестных ссылок на иллюстрации и\n- текст") == ["пункт-списка"]
    assert rules("- для указания буквенных обозначений\n  или") == ["пункт-списка"]
    assert rules("- слово `код` и `код`") == []

    # 8.2.3.18: слова «ссылка» в тексте ссылки.
    assert rules("Нажмите на [ссылку](https://example.com), чтобы узнать больше.") == ["текст-ссылки"]
    assert rules("Требования — в [ГОСТ Р 58049—2017](https://example.com).") == []

    # Код, служебный заголовок и таблицы.
    assert rules("```\n" + " ".join(["слово"] * 40) + ".\n```") == []
    front = "---\nname: x\ndescription: " + " ".join(["слово"] * 30) + "\n---\nСлово."
    assert rules(front) == []
    table = ("| Пункт | Текст |\n|---|---|\n| 8.2.3.3 | " + " ".join(["слово"] * 19)
             + ". |\n| 8.2.6.2 | " + " ".join(["слово"] * 21) + ". |")
    assert rules(table) == ["длина"], check(table)

    # Советы на исходных текстах из примеров ГОСТа.
    assert "причастие" in rules("Опознавательные знаки, расположенные на самолете, "
                                "подразделяются на основной и дополнительные.")
    translator = ("Переводчик, выполнивший перевод нормативного документа, "
                  "по выданным замечаниям осуществляет корректировку перевода.")
    assert {"причастие", "отглагольное"} <= set(rules(translator)), rules(translator)
    assert "деепричастие" in rules("Диаметр клипсы выбирается, учитывая размер самой трубы.")
    assert "залог" in rules("Работа выполняется пользователем в соответствии "
                            "с национальными авиационными правилами авиационных властей.")
    assert "отглагольное" in rules("Для оформления протокола по результатам "
                                   "осуществляют периодический контроль параметров ТО.")
    assert "отглагольное" not in rules("После аварийного приземления и остановки самолета "
                                       "выполняйте команды командира экипажа.")

    # Слова «может», «следует», «должно» не отмечаются никогда.
    modal = ("В случае перевода с русского языка на иностранный исходный текст может быть "
             "отредактирован согласно требованиям УТР. Скобки следует использовать для "
             "перекрестных ссылок. Текст должен быть прост для восприятия и понимания.")
    assert check(modal) == [], check(modal)

    findings = check(" ".join(["слово"] * 21) + ".", filename="ответ.md")
    assert findings[0]["file"] == "ответ.md" and findings[0]["clause"] == "8.2.5.1, 8.2.6.1"
    print("Самопроверка пройдена.")


def main(argv):
    if "--selftest" in argv:
        selftest()
        return 0
    as_json = "--json" in argv
    paths = [a for a in argv if a != "--json"] or ["-"]
    findings = []
    for path in paths:
        if path == "-":
            sys.stdin.reconfigure(encoding="utf-8")
            findings.extend(check(sys.stdin.read(), "-"))
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                findings.extend(check(handle.read(), path))
        except OSError as error:
            print(f"Не удалось прочитать {path}: {error.strerror}", file=sys.stderr)
            return 2
    return 1 if report(findings, as_json) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
