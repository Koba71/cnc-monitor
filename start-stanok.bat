@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem === Настройки этого ПК станка (поправьте под себя) ===
set MACHINE_ID=edm-1
set MACHINE_NAME=Эрозия 1
set PORT=8080

echo Запуск монитора станка %MACHINE_NAME% (%MACHINE_ID%)
echo Сначала должен быть открыт AutoCut.
echo Остановка: Ctrl+C в этом окне.
echo.

python monitor.py --dashboard --bind 0.0.0.0 --port %PORT% --id %MACHINE_ID% --name "%MACHINE_NAME%"
if errorlevel 1 (
  echo.
  echo Не удалось запустить. Проверьте Python и команду:
  echo   python -m pip install -r requirements.txt
  pause
)
