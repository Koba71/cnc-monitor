# -*- coding: utf-8 -*-
"""Разбор текстов окна AutoCut: координаты, время, скорость, номер детали."""

import re

CSV_COLUMNS = [
    "Timestamp",
    "X",
    "Y",
    "WorkingTime",
    "SurplusTime",
    "Speed",
    "DetailNo",
]

# Координаты вида "X:-0016527" / "Y:00087044" (допускается китайское двоеточие)
RE_COORD_X = re.compile(r"X[:：]\s*([-+]?\d+)", re.IGNORECASE)
RE_COORD_Y = re.compile(r"Y[:：]\s*([-+]?\d+)", re.IGNORECASE)

# Время вида "006:34:39" (часы могут быть 1–3 цифры)
RE_TIME = re.compile(r"(\d{1,3}:\d{2}:\d{2})")

# Скорость/частота: "<35.0Hz", "35.0 Hz", "35Hz"
RE_SPEED = re.compile(r"[<＜]?\s*(\d+(?:[.,]\d+)?)\s*Hz", re.IGNORECASE)

# Номер детали: "No.1", "No. 12", "No:1"
RE_DETAIL = re.compile(r"No\.?\s*[:：]?\s*(\d+)", re.IGNORECASE)

RE_WORKING_LABEL = re.compile(r"Working\s*Time", re.IGNORECASE)
RE_SURPLUS_LABEL = re.compile(r"Surplus\s*Time", re.IGNORECASE)


def raw_text(key):
    """Убирает суффикс дубликата ' [2]', добавленный при сборе Static."""
    return re.sub(r"\s\[\d+\]$", "", key)


def find_nearby_value(label_rect, items, pattern):
    """
    Ищет рядом с меткой (справа или ниже) Static, чей текст подходит под pattern.

    Нужен, когда подпись («Working Time») и значение («006:34:39»)
    находятся в разных элементах Static.
    """
    if not label_rect:
        return None

    lx, ly, lr, lb = label_rect
    label_cy = (ly + lb) / 2.0
    candidates = []

    for text, rect in items:
        match = pattern.search(text)
        if not match:
            continue
        rx, ry, rr, rb = rect
        value_cy = (ry + rb) / 2.0
        same_row = abs(label_cy - value_cy) <= 30
        to_the_right = rx >= lr - 8
        same_col = abs(lx - rx) <= 50
        below = ry >= lb - 8
        if (same_row and to_the_right) or (same_col and below):
            dist = abs(rx - lr) + abs(ry - ly)
            candidates.append((dist, match.group(1)))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def parse_data(texts_dict):
    """
    Извлекает из словаря текстов координаты, время, скорость и номер детали.

    Сначала пытается разобрать текст внутри одного Static (подпись + значение),
    затем, если поля пустые, ищет значение в соседнем Static по координатам.
    """
    data = {
        "X": "",
        "Y": "",
        "WorkingTime": "",
        "SurplusTime": "",
        "Speed": "",
        "DetailNo": "",
    }

    items = [(raw_text(key), rect) for key, rect in (texts_dict or {}).items()]

    working_label_rect = None
    surplus_label_rect = None
    speed_label_rect = None
    detail_label_rect = None
    x_label_rect = None
    y_label_rect = None

    for text, rect in items:
        if not data["X"]:
            match = RE_COORD_X.search(text)
            if match:
                data["X"] = match.group(1)
        if re.match(r"^X[:：]?\s*$", text.strip(), re.IGNORECASE):
            x_label_rect = rect

        if not data["Y"]:
            match = RE_COORD_Y.search(text)
            if match:
                data["Y"] = match.group(1)
        if re.match(r"^Y[:：]?\s*$", text.strip(), re.IGNORECASE):
            y_label_rect = rect

        if RE_WORKING_LABEL.search(text):
            working_label_rect = rect
            match = re.search(
                r"Working\s*Time[:：\s]+(\d{1,3}:\d{2}:\d{2})",
                text,
                re.IGNORECASE,
            )
            if match:
                data["WorkingTime"] = match.group(1)

        if RE_SURPLUS_LABEL.search(text):
            surplus_label_rect = rect
            match = re.search(
                r"Surplus\s*Time[:：\s]+(\d{1,3}:\d{2}:\d{2})",
                text,
                re.IGNORECASE,
            )
            if match:
                data["SurplusTime"] = match.group(1)

        if not data["Speed"]:
            match = RE_SPEED.search(text)
            if match:
                data["Speed"] = match.group(1).replace(",", ".")
        if "speed" in text.lower() or "частота" in text.lower():
            speed_label_rect = rect

        if not data["DetailNo"]:
            match = RE_DETAIL.search(text)
            if match:
                data["DetailNo"] = match.group(1)
        if re.search(r"\bNo\.?\b", text, re.IGNORECASE) and not RE_DETAIL.search(text):
            detail_label_rect = rect

    coord_number = re.compile(r"([-+]?\d+)")
    if not data["X"] and x_label_rect:
        nearby = find_nearby_value(x_label_rect, items, coord_number)
        if nearby:
            data["X"] = nearby
    if not data["Y"] and y_label_rect:
        nearby = find_nearby_value(y_label_rect, items, coord_number)
        if nearby:
            data["Y"] = nearby

    if not data["WorkingTime"] and working_label_rect:
        nearby = find_nearby_value(working_label_rect, items, RE_TIME)
        if nearby:
            data["WorkingTime"] = nearby

    if not data["SurplusTime"] and surplus_label_rect:
        nearby = find_nearby_value(surplus_label_rect, items, RE_TIME)
        if nearby:
            data["SurplusTime"] = nearby

    if not data["Speed"] and speed_label_rect:
        nearby = find_nearby_value(speed_label_rect, items, RE_SPEED)
        if nearby:
            data["Speed"] = nearby.replace(",", ".")

    if not data["DetailNo"] and detail_label_rect:
        nearby = find_nearby_value(
            detail_label_rect, items, re.compile(r"(\d+)")
        )
        if nearby:
            data["DetailNo"] = nearby

    return data


def has_any_value(data):
    """True, если удалось извлечь хотя бы одно поле станка."""
    return any(data.get(key) for key in CSV_COLUMNS if key != "Timestamp")
