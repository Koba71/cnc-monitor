@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "machines.json" (
  echo Нет файла machines.json
  echo Скопируйте machines.example.json в machines.json и пропишите IP станков.
  echo Подробности: INSTALL.md раздел 4.
  pause
  exit /b 1
)

echo Обзор цеха. Остановка: Ctrl+C
echo.

python monitor.py --dashboard-only --machines machines.json --port 8080
if errorlevel 1 (
  echo.
  echo Не удалось запустить панель. Проверьте Python и machines.json
  pause
)
