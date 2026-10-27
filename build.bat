@echo off
setlocal
cd /d "%~dp0"
call .venv\Scripts\activate || goto :err
for /f %%v in ('python -c "import ralenti;print(ralenti.__version__)"') do set VER=%%v
set "NAME=Ralenti"
set "ISSARGS="
if exist ralenti_ext\build_vars.bat call ralenti_ext\build_vars.bat
echo Building %NAME% %VER%
python scripts\make_icon.py || goto :err
rmdir /s /q dist\Ralenti 2>nul
pyinstaller --noconfirm --clean ralenti.spec || goto :err
rmdir /s /q build 2>nul
copy /y LICENSE dist\Ralenti\ >nul
copy /y THIRD_PARTY_NOTICES.md dist\Ralenti\ >nul
del /q dist\%NAME%-%VER%-portable.zip 2>nul
powershell -NoProfile -Command "Compress-Archive -Path dist\Ralenti -DestinationPath dist\%NAME%-%VER%-portable.zip" || goto :err

set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC goto :noinno
"%ISCC%" /Qp /DAppVersion=%VER% %ISSARGS% installer\ralenti.iss || goto :err
echo.
echo Installer: dist\%NAME%-%VER%-setup.exe
goto :done

:noinno
echo.
echo Inno Setup 6 not found, installer skipped. Install it with:  winget install JRSoftware.InnoSetup

:done
echo Portable:  dist\%NAME%-%VER%-portable.zip
echo App:       dist\Ralenti\Ralenti.exe
explorer dist
exit /b 0
:err
echo Build failed.
exit /b 1
