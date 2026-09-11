# -*- coding: utf-8 -*-
"""
Мониторинг управляющей программы электроэрозионного станка ЧПУ.

Скрипт находит окно процесса (по умолчанию AutoCut.exe), читает тексты
из дочерних элементов класса Static, извлекает координаты, время,
скорость и номер детали, затем дописывает снимок в CSV.

С флагом --dashboard поднимается локальная панель обзора цеха
(плитки станков по мотивам открытого Veyon Master).
"""

from __future__ import print_function

import argparse
import csv
import ctypes
import json
import os
import sys
import tempfile
import time
import webbrowser
from datetime import datetime

from parse import CSV_COLUMNS, has_any_value, parse_data
from dashboard import (
    WEB_DIR_NAME,
    ShopState,
    load_machines_file,
    start_dashboard,
)

# =============================================================================
# НАСТРОЙКИ
# =============================================================================

# Имя исполняемого файла управляющей программы
PROCESS_NAME = "AutoCut.exe"

# Имя станка на плитке обзора
MACHINE_NAME = "Станок ЧПУ"

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

# Снимок окна AutoCut для плитки обзора (BMP)
PREVIEW_FILENAME = "preview.bmp"
STATE_FILENAME = "state.json"

# WinAPI
user32 = ctypes.windll.user32 if os.name == "nt" else None
SMTO_ABORTIFHUNG = 0x0002
DWORD_PTR = ctypes.c_size_t
PW_RENDERFULLCONTENT = 2

try:
    import psutil
    import win32con
    import win32gui
    import win32process
    import win32ui

    WINDOWS_MONITOR = True
except ImportError:
    WINDOWS_MONITOR = False


def _script_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


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


# =============================================================================
# 3. Снимок окна для плитки обзора
# =============================================================================

def capture_window_bmp(hwnd):
    """
    Снимает окно через PrintWindow (видно и когда оно не поверх остальных).

    Возвращает байты BMP или None.
    """
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception:
        return None

    width = right - left
    height = bottom - top
    if width < 8 or height < 8:
        return None

    hwnd_dc = None
    mfc_dc = None
    save_dc = None
    bitmap = None
    tmp_path = None
    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)

        printed = user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not printed:
            printed = user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)
        if not printed:
            save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)

        fd, tmp_path = tempfile.mkstemp(suffix=".bmp")
        os.close(fd)
        bitmap.SaveBitmapFile(save_dc, tmp_path)
        with open(tmp_path, "rb") as fh:
            return fh.read()
    except Exception as exc:
        _print_status("Не удалось снять окно: %s" % exc)
        return None
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        try:
            if save_dc is not None:
                save_dc.DeleteDC()
        except Exception:
            pass
        try:
            if mfc_dc is not None:
                mfc_dc.DeleteDC()
        except Exception:
            pass
        if hwnd_dc:
            try:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
            except Exception:
                pass


# =============================================================================
# 4. Запись в CSV и JSON
# =============================================================================

def _csv_path():
    return os.path.join(_script_dir(), CSV_FILENAME)


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


def write_state_file(payload):
    path = os.path.join(_script_dir(), STATE_FILENAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
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


def _empty_fields():
    return {
        "X": "",
        "Y": "",
        "WorkingTime": "",
        "SurplusTime": "",
        "Speed": "",
        "DetailNo": "",
    }


def _publish(shop_state, fields, preview=None, write_json=True):
    shop_state.update_local(fields, preview=preview, preview_type="image/bmp")
    if write_json:
        try:
            write_state_file(shop_state.snapshot_local())
        except OSError as exc:
            _print_status("Не удалось записать %s: %s" % (STATE_FILENAME, exc))
    if preview:
        preview_path = os.path.join(_script_dir(), PREVIEW_FILENAME)
        try:
            with open(preview_path, "wb") as fh:
                fh.write(preview)
        except OSError as exc:
            _print_status("Не удалось записать снимок: %s" % exc)


# =============================================================================
# 5. Основной цикл
# =============================================================================

def poll_loop(shop_state, write_csv, capture_preview):
    csv_path = _csv_path()
    _print_status("Мониторинг ЧПУ запущен")
    _print_status("Процесс: %s" % PROCESS_NAME)
    _print_status("Интервал: %s с" % POLL_INTERVAL_SEC)
    if write_csv:
        _print_status("CSV: %s" % csv_path)
    _print_status("Остановка: Ctrl+C")
    print("", flush=True)

    window_was_missing = True
    debug_printed = False

    while True:
        try:
            hwnd = find_main_window_by_process(PROCESS_NAME)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if not hwnd or not win32gui.IsWindow(hwnd):
                _print_status(
                    "Окно процесса %s не найдено, повтор через %s с..."
                    % (PROCESS_NAME, POLL_INTERVAL_SEC)
                )
                window_was_missing = True
                payload = _empty_fields()
                payload.update({
                    "online": True,
                    "window_found": False,
                    "has_data": False,
                    "updated_at": now,
                    "window_title": "",
                    "error": "окно не найдено",
                })
                _publish(shop_state, payload)
                time.sleep(POLL_INTERVAL_SEC)
                continue

            try:
                title = win32gui.GetWindowText(hwnd) or "(без заголовка)"
            except Exception:
                title = "(не удалось прочитать заголовок)"

            if window_was_missing:
                _print_status("Окно найдено: hwnd=%s  «%s»" % (hwnd, title))
                window_was_missing = False
                debug_printed = False

            texts = get_static_texts(hwnd)

            if DEBUG and not debug_printed:
                _print_debug_texts(texts)
                debug_printed = True

            preview = capture_window_bmp(hwnd) if capture_preview else None

            if not texts:
                _print_status(
                    "Static-элементы пусты или недоступны, повтор через %s с..."
                    % POLL_INTERVAL_SEC
                )
                payload = _empty_fields()
                payload.update({
                    "online": True,
                    "window_found": True,
                    "has_data": False,
                    "updated_at": now,
                    "window_title": title,
                    "error": "нет Static",
                })
                _publish(shop_state, payload, preview=preview)
                time.sleep(POLL_INTERVAL_SEC)
                continue

            parsed = parse_data(texts)
            parsed["Timestamp"] = now

            if not has_any_value(parsed):
                _print_status(
                    "Данные станка не распознаны (Static: %d шт.). "
                    "Включите DEBUG = True в начале monitor.py, чтобы увидеть сырые тексты."
                    % len(texts)
                )
                if not debug_printed:
                    _print_debug_texts(texts)
                    debug_printed = True
                payload = dict(parsed)
                payload.update({
                    "online": True,
                    "window_found": True,
                    "has_data": False,
                    "updated_at": now,
                    "window_title": title,
                    "error": "данные не распознаны",
                })
                _publish(shop_state, payload, preview=preview)
                time.sleep(POLL_INTERVAL_SEC)
                continue

            if write_csv:
                log_data(parsed)
            _print_status(_format_snapshot(parsed))

            payload = dict(parsed)
            payload.update({
                "online": True,
                "window_found": True,
                "has_data": True,
                "updated_at": now,
                "window_title": title,
                "error": "",
            })
            _publish(shop_state, payload, preview=preview)

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            _print_status("Ошибка опроса (продолжаем): %s" % exc)
            payload = _empty_fields()
            payload.update({
                "online": True,
                "window_found": False,
                "has_data": False,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(exc),
            })
            _publish(shop_state, payload)

        try:
            time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            raise


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Мониторинг окна AutoCut и обзор цеха (по мотивам Veyon)."
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Открыть веб-обзор станков (плитки экранов и телеметрия).",
    )
    parser.add_argument(
        "--dashboard-only",
        action="store_true",
        help="Только панель обзора, без чтения AutoCut (для офисного ПК).",
    )
    parser.add_argument("--port", type=int, default=8080, help="Порт веб-обзора.")
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="Адрес прослушивания. Для доступа из цеха: 0.0.0.0",
    )
    parser.add_argument("--name", default=MACHINE_NAME, help="Имя станка на плитке.")
    parser.add_argument(
        "--id",
        dest="machine_id",
        default="local",
        help="Идентификатор станка в обзоре.",
    )
    parser.add_argument(
        "--machines",
        default="",
        help="JSON со списком удалённых станков (см. machines.example.json).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Не открывать браузер при запуске панели.",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Не писать machine_log.csv.",
    )
    return parser.parse_args(argv)


def _load_remotes(machines_path, shop_state):
    path = machines_path
    if not path:
        candidate = os.path.join(_script_dir(), "machines.json")
        if os.path.isfile(candidate):
            path = candidate
    if not path:
        return
    if not os.path.isfile(path):
        _print_status("Файл списка станков не найден: %s" % path)
        return
    title, remotes = load_machines_file(path)
    with shop_state.lock:
        if title:
            shop_state.title = title
        shop_state.remotes = remotes
    _print_status("Загружен список станков: %s (%d шт.)" % (path, len(remotes)))


def main(argv=None):
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    dashboard_only = args.dashboard_only
    want_dashboard = args.dashboard or dashboard_only

    shop_state = ShopState()
    shop_state.title = "Обзор цеха ЧПУ"
    with shop_state.lock:
        shop_state.local["id"] = args.machine_id
        shop_state.local["name"] = args.name
        shop_state.local["process"] = PROCESS_NAME

    _load_remotes(args.machines, shop_state)

    with shop_state.lock:
        # Офисная панель скрывает «этот ПК» только если уже есть удалённые станки.
        shop_state.include_local = not (dashboard_only and bool(shop_state.remotes))

    if dashboard_only:
        shop_state.update_local({"online": False, "error": "режим только панели"})

    if want_dashboard:
        web_root = os.path.join(_script_dir(), WEB_DIR_NAME)
        if not os.path.isdir(web_root):
            _print_status("Нет папки веб-интерфейса: %s" % web_root)
            return 1
        try:
            _server, _thread, bound_port = start_dashboard(
                shop_state, args.bind, args.port, web_root
            )
        except OSError as exc:
            _print_status("Не удалось запустить веб-обзор: %s" % exc)
            return 1
        url = "http://%s:%s/" % (
            "127.0.0.1" if args.bind == "0.0.0.0" else args.bind,
            bound_port,
        )
        _print_status("Веб-обзор: %s" % url)
        if args.bind == "0.0.0.0":
            _print_status("Панель слушает все интерфейсы (доступ из локальной сети).")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass

    if dashboard_only:
        _print_status("Режим панели без опроса AutoCut. Остановка: Ctrl+C")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("", flush=True)
            _print_status("Остановлено пользователем (Ctrl+C)")
            return 0

    if not WINDOWS_MONITOR:
        _print_status(
            "Чтение AutoCut доступно только на Windows (нужны pywin32 и psutil). "
            "Для офисного обзора запустите: python monitor.py --dashboard-only --machines machines.json"
        )
        return 1

    try:
        poll_loop(
            shop_state,
            write_csv=not args.no_csv,
            capture_preview=want_dashboard,
        )
    except KeyboardInterrupt:
        print("", flush=True)
        _print_status("Остановлено пользователем (Ctrl+C)")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
