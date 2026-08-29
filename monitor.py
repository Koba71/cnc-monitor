# -*- coding: utf-8 -*-
"""
Мониторинг управляющей программы электроэрозионного станка ЧПУ.

Скрипт находит окно процесса (по умолчанию AutoCut.exe), читает тексты
из дочерних элементов класса Static, извлекает координаты, время,
скорость и номер детали, затем дописывает снимок в CSV.
"""

import csv
import ctypes
import os
import re
import sys
import time
from datetime import datetime

import psutil
import win32con
import win32gui
import win32process

# =============================================================================
# НАСТРОЙКИ
# =============================================================================

# Имя исполняемого файла управляющей программы
PROCESS_NAME = "AutoCut.exe"

# Интервал опроса окна, секунды (рекомендуется 2–5)
POLL_INTERVAL_SEC = 3

# Имя CSV-файла (создаётся рядом со скриптом)
CSV_FILENAME = "machine_log.csv"

# Разделитель колонок в CSV (точка с запятой удобна для Excel в русской локали)
CSV_DELIMITER = ";"

# Если True — в консоль печатаются все найденные тексты Static (для отладки)
DEBUG = False

# Таймаут чтения текста из чужого процесса, мс
GETTEXT_TIMEOUT_MS = 200

# Колонки CSV в заданном порядке
CSV_COLUMNS = [
    "Timestamp",
    "X",
    "Y",
    "WorkingTime",
    "SurplusTime",
    "Speed",
    "DetailNo",
]

# =============================================================================
# Регулярные выражения
# =============================================================================

# Координаты вида "X:-0016527" / "Y:00087044" (допускается китайское двоеточие)
RE_COORD_X = re.compile(r"X[:：]\s*([-+]?\d+)", re.IGNORECASE)
RE_COORD_Y = re.compile(r"Y[:：]\s*([-+]?\d+)", re.IGNORECASE)

# Время вида "006:34:39" (часы могут быть 1–3 цифры)
RE_TIME = re.compile(r"(\d{1,3}:\d{2}:\d{2})")

# Скорость/частота: "<35.0Hz", "35.0 Hz", "35Hz"
RE_SPEED = re.compile(r"[<＜]?\s*(\d+(?:[.,]\d+)?)\s*Hz", re.IGNORECASE)

# Номер детали: "No.1", "No. 12", "No:1"
RE_DETAIL = re.compile(r"No\.?\s*[:：]?\s*(\d+)", re.IGNORECASE)

# Метки времени в тексте (англ. и возможные варианты)
RE_WORKING_LABEL = re.compile(r"Working\s*Time", re.IGNORECASE)
RE_SURPLUS_LABEL = re.compile(r"Surplus\s*Time", re.IGNORECASE)

# WinAPI
user32 = ctypes.windll.user32
SMTO_ABORTIFHUNG = 0x0002
# DWORD_PTR на 64-битной Windows — 64 бита
DWORD_PTR = ctypes.c_size_t


# =============================================================================
# 1. Поиск главного окна процесса
# =============================================================================

def find_main_window_by_process(process_name):
    """
    Находит главное видимое окно процесса по имени .exe-файла.

    Возвращает hwnd лучшего кандидата или None, если процесс/окно не найдены.
    При нескольких окнах выбирается самое большое видимое окно с заголовком.
    """
    target = (process_name or "").lower()
    pids = set()

    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = proc.info.get("name") or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if name.lower() == target:
                pids.add(proc.info["pid"])
    except Exception as exc:
        _print_status("Ошибка при перечислении процессов: %s" % exc)
        return None

    if not pids:
        return None

    windows = []

    def callback(hwnd, hwnds):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.IsIconic(hwnd):
                # Свёрнутое окно тоже подходит — текст в Static обычно доступен
                pass
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid in pids:
                hwnds.append(hwnd)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(callback, windows)
    except Exception as exc:
        _print_status("Ошибка EnumWindows: %s" % exc)
        return None

    if not windows:
        return None

    def score(hwnd):
        """Чем больше окно и информативнее заголовок, тем выше балл."""
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            area = max(0, right - left) * max(0, bottom - top)
            title = win32gui.GetWindowText(hwnd) or ""
            # Владелец = 0 обычно означает верхнее окно приложения
            owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
            owner_bonus = 1_000_000 if not owner else 0
            title_bonus = 100_000 if title.strip() else 0
            return area + owner_bonus + title_bonus
        except Exception:
            return 0

    windows.sort(key=score, reverse=True)
    return windows[0]


# =============================================================================
# 2. Чтение текстов из элементов Static
# =============================================================================

def _get_window_text(hwnd):
    """
    Читает текст окна/контрола, в том числе из другого процесса.

    GetWindowText для чужого процесса часто возвращает пустую строку
    у дочерних Static, поэтому используется SendMessageTimeout(WM_GETTEXT).
    Таймаут нужен, чтобы не зависнуть, если управляющая программа не отвечает.
    """
    length = 0
    length_out = DWORD_PTR()
    try:
        ok = user32.SendMessageTimeoutW(
            hwnd,
            win32con.WM_GETTEXTLENGTH,
            0,
            0,
            SMTO_ABORTIFHUNG,
            GETTEXT_TIMEOUT_MS,
            ctypes.byref(length_out),
        )
        if ok:
            length = int(length_out.value)
    except Exception:
        length = 0

    if length <= 0:
        try:
            return win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return ""

    buf = ctypes.create_unicode_buffer(length + 1)
    copied = DWORD_PTR()
    try:
        ok = user32.SendMessageTimeoutW(
            hwnd,
            win32con.WM_GETTEXT,
            length + 1,
            buf,
            SMTO_ABORTIFHUNG,
            GETTEXT_TIMEOUT_MS,
            ctypes.byref(copied),
        )
        if ok and buf.value:
            return buf.value
    except Exception:
        pass

    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""


def get_static_texts(hwnd):
    """
    Собирает тексты всех дочерних элементов класса Static.

    Возвращает словарь: {текст: (left, top, right, bottom)}.
    Если один и тот же текст встречается несколько раз, к ключу
    добавляется суффикс, чтобы не потерять дубликаты (например, два времени).
    """
    result = {}

    def enum_child(hwnd_child, _lparam):
        try:
            class_name = win32gui.GetClassName(hwnd_child)
        except Exception:
            return True

        # Некоторые сборки Windows дают "STATIC" в другом регистре
        if class_name.lower() != "static":
            return True

        text = (_get_window_text(hwnd_child) or "").strip()
        if not text:
            return True

        try:
            rect = win32gui.GetWindowRect(hwnd_child)
        except Exception:
            rect = (0, 0, 0, 0)

        key = text
        n = 2
        while key in result:
            key = "%s [%s]" % (text, n)
            n += 1
        result[key] = rect
        return True

    try:
        win32gui.EnumChildWindows(hwnd, enum_child, None)
    except Exception as exc:
        _print_status("Ошибка EnumChildWindows: %s" % exc)

    return result


def _raw_text(key):
    """Убирает суффикс дубликата ' [2]', добавленный в get_static_texts."""
    return re.sub(r"\s\[\d+\]$", "", key)


# =============================================================================
# 3. Парсинг данных
# =============================================================================

def _find_nearby_value(label_rect, items, pattern):
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

    items = [(_raw_text(key), rect) for key, rect in (texts_dict or {}).items()]

    working_label_rect = None
    surplus_label_rect = None
    speed_label_rect = None
    detail_label_rect = None
    x_label_rect = None
    y_label_rect = None

    for text, rect in items:
        # --- координаты ---
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

        # --- Working Time в той же строке ---
        if RE_WORKING_LABEL.search(text):
            working_label_rect = rect
            match = re.search(
                r"Working\s*Time[:：\s]+(\d{1,3}:\d{2}:\d{2})",
                text,
                re.IGNORECASE,
            )
            if match:
                data["WorkingTime"] = match.group(1)

        # --- Surplus Time в той же строке ---
        if RE_SURPLUS_LABEL.search(text):
            surplus_label_rect = rect
            match = re.search(
                r"Surplus\s*Time[:：\s]+(\d{1,3}:\d{2}:\d{2})",
                text,
                re.IGNORECASE,
            )
            if match:
                data["SurplusTime"] = match.group(1)

        # --- скорость ---
        if not data["Speed"]:
            match = RE_SPEED.search(text)
            if match:
                data["Speed"] = match.group(1).replace(",", ".")
        if "speed" in text.lower() or "частота" in text.lower():
            speed_label_rect = rect

        # --- номер детали ---
        if not data["DetailNo"]:
            match = RE_DETAIL.search(text)
            if match:
                data["DetailNo"] = match.group(1)
        if re.search(r"\bNo\.?\b", text, re.IGNORECASE) and not RE_DETAIL.search(text):
            detail_label_rect = rect

    # Подписи и значения в разных Static — ищем соседа
    coord_number = re.compile(r"([-+]?\d+)")
    if not data["X"] and x_label_rect:
        nearby = _find_nearby_value(x_label_rect, items, coord_number)
        if nearby:
            data["X"] = nearby
    if not data["Y"] and y_label_rect:
        nearby = _find_nearby_value(y_label_rect, items, coord_number)
        if nearby:
            data["Y"] = nearby

    if not data["WorkingTime"] and working_label_rect:
        nearby = _find_nearby_value(working_label_rect, items, RE_TIME)
        if nearby:
            data["WorkingTime"] = nearby

    if not data["SurplusTime"] and surplus_label_rect:
        nearby = _find_nearby_value(surplus_label_rect, items, RE_TIME)
        if nearby:
            data["SurplusTime"] = nearby

    if not data["Speed"] and speed_label_rect:
        nearby = _find_nearby_value(speed_label_rect, items, RE_SPEED)
        if nearby:
            data["Speed"] = nearby.replace(",", ".")

    if not data["DetailNo"] and detail_label_rect:
        nearby = _find_nearby_value(
            detail_label_rect, items, re.compile(r"(\d+)")
        )
        if nearby:
            data["DetailNo"] = nearby

    return data


def has_any_value(data):
    """True, если удалось извлечь хотя бы одно поле станка."""
    return any(data.get(key) for key in CSV_COLUMNS if key != "Timestamp")


# =============================================================================
# 4. Запись в CSV
# =============================================================================

def _csv_path():
    """Полный путь к CSV рядом со скриптом (или в текущей папке)."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, CSV_FILENAME)


def log_data(data):
    """
    Дописывает одну строку в CSV.

    Если файла ещё нет — создаёт его с заголовком.
    Кодировка utf-8-sig, чтобы Excel на Windows открыл кириллицу корректно.
    """
    path = _csv_path()
    file_exists = os.path.isfile(path)

    row = {col: data.get(col, "") for col in CSV_COLUMNS}
    row["Timestamp"] = data.get("Timestamp") or datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    with open(path, "a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=CSV_COLUMNS,
            delimiter=CSV_DELIMITER,
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    return path


# =============================================================================
# Служебный вывод
# =============================================================================

def _print_status(message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("[%s] %s" % (stamp, message), flush=True)


def _format_snapshot(data):
    parts = []
    for key in CSV_COLUMNS:
        if key == "Timestamp":
            continue
        value = data.get(key) or "—"
        parts.append("%s=%s" % (key, value))
    return "  ".join(parts)


def _print_debug_texts(texts_dict):
    print("----- найденные Static (%d шт.) -----" % len(texts_dict), flush=True)
    for text, rect in texts_dict.items():
        print("  %s  %s" % (rect, text), flush=True)
    print("------------------------------------", flush=True)


# =============================================================================
# 5. Основной цикл
# =============================================================================

def main():
    csv_path = _csv_path()
    _print_status("Мониторинг ЧПУ запущен")
    _print_status("Процесс: %s" % PROCESS_NAME)
    _print_status("Интервал: %s с" % POLL_INTERVAL_SEC)
    _print_status("CSV: %s" % csv_path)
    _print_status("Остановка: Ctrl+C")
    print("", flush=True)

    window_was_missing = True
    debug_printed = False

    while True:
        try:
            hwnd = find_main_window_by_process(PROCESS_NAME)

            if not hwnd or not win32gui.IsWindow(hwnd):
                _print_status(
                    "Окно процесса %s не найдено, повтор через %s с..."
                    % (PROCESS_NAME, POLL_INTERVAL_SEC)
                )
                window_was_missing = True
                time.sleep(POLL_INTERVAL_SEC)
                continue

            if window_was_missing:
                try:
                    title = win32gui.GetWindowText(hwnd) or "(без заголовка)"
                except Exception:
                    title = "(не удалось прочитать заголовок)"
                _print_status("Окно найдено: hwnd=%s  «%s»" % (hwnd, title))
                window_was_missing = False
                debug_printed = False

            texts = get_static_texts(hwnd)

            if DEBUG and not debug_printed:
                _print_debug_texts(texts)
                debug_printed = True

            if not texts:
                _print_status(
                    "Static-элементы пусты или недоступны, повтор через %s с..."
                    % POLL_INTERVAL_SEC
                )
                time.sleep(POLL_INTERVAL_SEC)
                continue

            parsed = parse_data(texts)
            parsed["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if not has_any_value(parsed):
                _print_status(
                    "Данные станка не распознаны (Static: %d шт.). "
                    "Включите DEBUG = True в начале monitor.py, чтобы увидеть сырые тексты."
                    % len(texts)
                )
                if not debug_printed:
                    _print_debug_texts(texts)
                    debug_printed = True
                time.sleep(POLL_INTERVAL_SEC)
                continue

            log_data(parsed)
            _print_status(_format_snapshot(parsed))

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            _print_status("Ошибка опроса (продолжаем): %s" % exc)

        try:
            time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("", flush=True)
        _print_status("Остановлено пользователем (Ctrl+C)")
        sys.exit(0)
