# Мониторинг станка ЧПУ

Программа читает с экрана управляющей программы электроэрозионного станка (по умолчанию `AutoCut.exe`) координаты, время обработки, скорость и номер детали, затем пишет журнал CSV. Можно открыть **веб-обзор цеха**: плитки станков с превью окна AutoCut.

**Полная инструкция: куда что ставить и как настраивать — в файле [INSTALL.md](INSTALL.md).**

Кратко:

| Компьютер | Что поставить | Чем запускать |
|-----------|---------------|---------------|
| ПК у станка (Windows + AutoCut) | Python, эта папка, `pywin32` и `psutil` | `start-stanok.bat` |
| Офис / кабинет | Python, эта папка, файл `machines.json` с IP станков | `start-office.bat` |
| По желанию Veyon | Служба на станке, Master в кабинете | установщик с [veyon.io/download](https://veyon.io/download/) |

Veyon — отдельная открытая программа ([github.com/veyon/veyon](https://github.com/veyon/veyon), GPL-2.0). Она показывает живой рабочий стол, этот монитор — цифры AutoCut в CSV. Ставить оба не обязательно.

## Быстрый старт на одном ПК станка

1. Установите Python с галочкой **Add python.exe to PATH** (на Windows 7 — только [Python 3.8.10](https://www.python.org/downloads/release/python-3810/)).
2. Скопируйте папку программы, в ней выполните:

```bat
python -m pip install -r requirements.txt
```

3. Запустите AutoCut, затем:

```bat
python monitor.py
```

или для плитки в браузере (и доступа из сети):

```bat
python monitor.py --dashboard --bind 0.0.0.0 --name "Эрозия 1" --id edm-1
```

Журнал: `machine_log.csv` (разделитель `;`, колонки `Timestamp;X;Y;WorkingTime;SurplusTime;Speed;DetailNo`). Остановка: **Ctrl+C**.

Дальше — IP, брандмауэр, второй станок, офисная панель, автозапуск и Veyon: **[INSTALL.md](INSTALL.md)**.

## Если данные не считываются

1. AutoCut запущен, `PROCESS_NAME` в начале `monitor.py` совпадает с именем в диспетчере задач (вкладка «Подробности»).
2. Поставьте `DEBUG = True` и смотрите сырые тексты Static в консоли.
3. Если AutoCut запущен от администратора — монитор тоже.

## Проверка разбора данных

```bat
python -m unittest discover -s tests -v
```
