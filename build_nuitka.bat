@echo off
chcp 65001 >nul 2>&1
echo ====================================
echo Xiao8 Nuitka Build Tool
echo ====================================
echo.
echo Preparing Nuitka environment...

REM 1. Install Nuitka dependencies
echo Installing dependencies to .venv...
uv pip install nuitka ordered-set zstandard --python .venv
uv pip install onnxruntime tokenizers --python .venv

REM 2. Pre-install Playwright Chromium into project dir for Nuitka bundle (avoid on-site download)
if not exist "playwright_browsers" mkdir "playwright_browsers"
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\playwright_browsers"
echo Installing Playwright Chromium to playwright_browsers for bundle...
uv run python -m playwright install chromium
if %errorlevel% neq 0 (
    echo [WARNING] Playwright Chromium install failed; runtime may download on first run.
) else (
    echo Playwright Chromium cached for bundle.
)

REM 2b. Pre-download browser-use default extensions (users in China cannot reach Chrome Web Store)
echo Pre-downloading browser-use extensions for bundle...
uv run python -c "from browser_use.browser.profile import BrowserProfile; BrowserProfile()._ensure_default_extensions_downloaded()"
if not exist "data\browser_use_extensions" mkdir "data\browser_use_extensions"
uv run python -c "import shutil, os; from browser_use.config import CONFIG; src=str(CONFIG.BROWSER_USE_EXTENSIONS_DIR); dst='data/browser_use_extensions'; [shutil.copytree(os.path.join(src,d), os.path.join(dst,d), dirs_exist_ok=True) for d in os.listdir(src) if os.path.isdir(os.path.join(src,d))]"
if %errorlevel% neq 0 (
    echo [WARNING] Extension pre-download failed; runtime will attempt download.
) else (
    echo Browser-use extensions cached for bundle.
)

REM 3. Build all frontend projects
echo.
echo Building frontend projects...
where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] npm not found — skipping frontend builds.
) else (
    call build_frontend.bat
    if errorlevel 1 (
        echo [WARNING] Frontend build failed — /ui and react chat may be stale.
    ) else (
        echo Frontend projects built successfully.
    )
)
echo.

REM 3c. Pre-fetch tiktoken o200k_base blob for offline use (wheel has no .tiktoken files)
if not exist "data\tiktoken_cache" mkdir "data\tiktoken_cache"
set "TIKTOKEN_CACHE_DIR=%CD%\data\tiktoken_cache"
echo Warming tiktoken cache (o200k_base) into data\tiktoken_cache ...
uv run python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
if %errorlevel% neq 0 (
    echo [WARNING] tiktoken cache warm failed; frozen build may download on first use.
) else (
    echo tiktoken o200k_base cache ready.
)
set "TIKTOKEN_CACHE_DIR="

REM 3d. Download anonymous embedding profile assets for offline packaged use.
REM The concrete repo can be changed without exposing it to runtime config or user cache ids.
REM Pin upstream weights/tokenizer to a specific commit; the profile id is the
REM cache compatibility contract, so a moving branch would silently drift.
set "EMBEDDING_MODEL_REPO=jinaai/jina-embeddings-v5-text-nano-retrieval"
set "EMBEDDING_MODEL_PROFILE_ID=local-text-retrieval-v1"
set "EMBEDDING_MODEL_REVISION=ac5d898c8d382b17167c33e5c8af644a3519b47d"
echo Preparing embedding model profile %EMBEDDING_MODEL_PROFILE_ID% from %EMBEDDING_MODEL_REPO%@%EMBEDDING_MODEL_REVISION% ...
uv run python scripts\prepare_embedding_model.py --repo "%EMBEDDING_MODEL_REPO%" --revision "%EMBEDDING_MODEL_REVISION%" --profile-id "%EMBEDDING_MODEL_PROFILE_ID%" --output-root data\embedding_models --variant both
if %errorlevel% neq 0 (
    echo [ERROR] embedding model asset preparation failed.
    pause
    exit /b 1
) else (
    echo Embedding model assets ready for bundle.
)

REM 4. Clean old builds
if exist "build_nuitka" rmdir /s /q "build_nuitka"
if exist "dist\Xiao8" rmdir /s /q "dist\Xiao8"

REM 4. Start compilation
echo.
echo Compiling (this may take a long time, please wait)...
echo.

set NUITKA_OPTS=--standalone --output-dir="dist" --output-filename="projectneko_server.exe"
set NUITKA_OPTS=%NUITKA_OPTS% --windows-icon-from-ico=assets/icon.ico
set NUITKA_OPTS=%NUITKA_OPTS% --company-name="Project N.E.K.O."
set NUITKA_OPTS=%NUITKA_OPTS% --product-name="N.E.K.O. AI Assistant"
set NUITKA_OPTS=%NUITKA_OPTS% --file-version=1.0.0.0
set NUITKA_OPTS=%NUITKA_OPTS% --product-version=1.0.0.0
REM Include config files explicitly (exclude runtime-generated files)
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/__init__.py=config/__init__.py
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/api_providers.json=config/api_providers.json
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/characters.json=config/characters.json
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/characters.en.json=config/characters.en.json
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/characters.ja.json=config/characters.ja.json
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/characters.ko.json=config/characters.ko.json
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/characters.zh-CN.json=config/characters.zh-CN.json
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/characters.zh-TW.json=config/characters.zh-TW.json
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/core_config.json=config/core_config.json
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/prompts_chara.py=config/prompts_chara.py
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/prompts_sys.py=config/prompts_sys.py
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=config/user_preferences.json=config/user_preferences.json
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=config/changelog=config/changelog
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=assets=assets
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=templates=templates
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=data/browser_use_prompts=data/browser_use_prompts
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=data/browser_use_extensions=data/browser_use_extensions
if exist "data\tiktoken_cache" set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=data/tiktoken_cache=data/tiktoken_cache
if exist "data\embedding_models" set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=data/embedding_models=data/embedding_models
if exist "frontend\plugin-manager\dist" set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=frontend/plugin-manager/dist=frontend/plugin-manager/dist
if exist "plugin\plugins" set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=plugin/plugins=plugin/plugins
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=playwright_browsers=playwright_browsers
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=static=static
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-dir=docs/zh-CN/guide=docs/zh-CN/guide
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=uvicorn
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=fastapi
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=starlette
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=jinja2
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=websockets
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=main_server
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=memory_server
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=agent_server
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=config
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=plugin
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=plugin.plugins
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=brain
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=main_logic
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=main_routers
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=memory
@REM set NUITKA_OPTS=%NUITKA_OPTS% --include-package=plugin.core
@REM set NUITKA_OPTS=%NUITKA_OPTS% --include-package=plugin.sdk
@REM set NUITKA_OPTS=%NUITKA_OPTS% --include-package=plugin.runtime
@REM set NUITKA_OPTS=%NUITKA_OPTS% --include-package=plugin.api
@REM set NUITKA_OPTS=%NUITKA_OPTS% --include-package=plugin.server
REM Built-in plugins now import via plugin.plugins.* and must be compiled in.
set NUITKA_OPTS=%NUITKA_OPTS% --nofollow-import-to=local_server
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=utils
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=steamworks
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=steam_api64.dll=steam_api64.dll
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=steam_api64.lib=steam_api64.lib
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=SteamworksPy64.dll=SteamworksPy64.dll
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=steam_appid.txt=steam_appid.txt
set NUITKA_OPTS=%NUITKA_OPTS% --nofollow-import-to=audiolab
set NUITKA_OPTS=%NUITKA_OPTS% --nofollow-import-to=pyrnnoise
set NUITKA_OPTS=%NUITKA_OPTS% --include-data-files=.venv/Lib/site-packages/pyrnnoise/rnnoise.dll=pyrnnoise/rnnoise.dll
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=browser_use
set NUITKA_OPTS=%NUITKA_OPTS% --include-package-data=browser_use
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=browser_use_sdk
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=playwright
set NUITKA_OPTS=%NUITKA_OPTS% --include-package-data=playwright
set NUITKA_OPTS=%NUITKA_OPTS% --include-package-data=jinja2
set NUITKA_OPTS=%NUITKA_OPTS% --include-package-data=certifi
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=bilibili_api
set NUITKA_OPTS=%NUITKA_OPTS% --include-package-data=bilibili_api
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=tiktoken
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=tiktoken_ext
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=onnxruntime
set NUITKA_OPTS=%NUITKA_OPTS% --include-package=tokenizers
set NUITKA_OPTS=%NUITKA_OPTS% --enable-plugin=dill-compat
set NUITKA_OPTS=%NUITKA_OPTS% --nofollow-import-to=matplotlib
set NUITKA_OPTS=%NUITKA_OPTS% --nofollow-import-to=pytest
set NUITKA_OPTS=%NUITKA_OPTS% --windows-console-mode=force

uv run python -m nuitka %NUITKA_OPTS% launcher.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Nuitka compilation failed!
    pause
    exit /b 1
)

echo.
echo Compilation complete! Organizing files...

REM Rename output directory
if exist "dist\launcher.dist" (
    move "dist\launcher.dist" "dist\Xiao8"
)

REM 5. Clean runtime / user data from dist so it never ships to end-users
echo.
echo Cleaning runtime and user data from dist...
REM  ── plugin store databases (auto-recreated on first run)
for /r "dist\Xiao8\plugin\plugins" %%f in (*.db) do (
    echo   DEL %%f
    del /q "%%f"
)
REM  ── log files
for /r "dist\Xiao8\plugin\plugins" %%f in (*.log) do (
    echo   DEL %%f
    del /q "%%f"
)
REM  ── __pycache__ directories
for /d /r "dist\Xiao8\plugin\plugins" %%d in (__pycache__) do (
    if exist "%%d" (
        echo   RMDIR %%d
        rmdir /s /q "%%d"
    )
)
REM  ── NapCat user-specific configs (account numbers, tokens, passkeys)
uv run python -c "from pathlib import Path; import re, json; d=Path(r'dist\Xiao8\plugin\plugins\qq_auto_reply\NapCat.Shell\config'); [print(f'  DEL {f}') or f.unlink() for f in d.glob('*.json') if re.search(r'_\d+\.json$', f.name)] if d.exists() else None"
uv run python -c "from pathlib import Path; import json; p=Path(r'dist\Xiao8\plugin\plugins\qq_auto_reply\NapCat.Shell\config\webui.json'); p.write_text(json.dumps({'host':'::','port':6099,'token':'','loginRate':10,'autoLoginAccount':''},indent=4)) or print(f'  RESET {p}') if p.exists() else None"
uv run python -c "from pathlib import Path; p=Path(r'dist\Xiao8\plugin\plugins\qq_auto_reply\NapCat.Shell\config\passkey.json'); p.write_text('{}') or print(f'  RESET {p}') if p.exists() else None"
echo Runtime data cleaned.

echo.
echo ====================================
echo Nuitka build complete!
echo Location: dist\Xiao8\projectneko_server.exe
echo ====================================
echo.

REM Sign the executable
echo Signing projectneko_server.exe...
set AZURE_TENANT_ID=71451461-49ce-42e2-b4ae-a965434e3cf5
signtool.exe sign /v /fd SHA256 /tr "http://timestamp.acs.microsoft.com" /td "SHA256" /dlib "C:\Users\wehos\AppData\Local\Microsoft\MicrosoftTrustedSigningClientTools\Azure.CodeSigning.Dlib.dll" /dmdf ".\metadata.package.json" "dist\Xiao8\projectneko_server.exe"

REM Step: Optional - copy artifacts into the lanlan_frd Electron host project for final packaging.
REM TARGET_DIR can be overridden via env var; if unset/missing the Nuitka standalone bundle at
REM dist\Xiao8 is the final artifact and we skip the Electron packaging steps gracefully.
if "%TARGET_DIR%"=="" set "TARGET_DIR=C:\Users\wehos\Project\lanlan_release\lanlan_frd"
echo.
if not exist "%TARGET_DIR%" (
    echo [INFO] TARGET_DIR not present: %TARGET_DIR%
    echo [INFO] Skipping lanlan_frd Electron packaging steps.
    echo Final Nuitka artifact: dist\Xiao8
    goto :final_done
)
echo Cleaning target directory: %TARGET_DIR%\bin
if exist "%TARGET_DIR%\bin" (
    echo Deleting old bin folder...
    rmdir /s /q "%TARGET_DIR%\bin"
)
echo Cleanup completed!
echo.

REM Step: Copy dist\Xiao8 as lanlan_frd\bin
echo Copying dist\Xiao8 to %TARGET_DIR%\bin...
xcopy /e /i /y "dist\Xiao8" "%TARGET_DIR%\bin"
if %errorlevel% neq 0 (
    echo [ERROR] Failed to copy dist\Xiao8 to bin folder!
    pause
    exit /b 1
)
echo Files copied successfully!
echo.

REM Step: Sign vendor/openfang/openfang.exe before Electron packaging
echo Signing vendor/openfang/openfang.exe...
set AZURE_TENANT_ID=71451461-49ce-42e2-b4ae-a965434e3cf5
signtool.exe sign /v /fd SHA256 /tr "http://timestamp.acs.microsoft.com" /td "SHA256" /dlib "C:\Users\wehos\AppData\Local\Microsoft\MicrosoftTrustedSigningClientTools\Azure.CodeSigning.Dlib.dll" /dmdf ".\metadata.package.json" "%TARGET_DIR%\vendor\openfang\openfang.exe"
if %errorlevel% neq 0 (
    echo [WARNING] Failed to sign openfang.exe, continuing...
)
echo.

REM Step: Run npm run dist in lanlan_frd directory
echo Building final package in lanlan_frd...
cd /d "%TARGET_DIR%"
call npm run dist
if %errorlevel% neq 0 (
    echo [ERROR] npm run dist failed!
    cd /d "%~dp0"
    pause
    exit /b 1
)
cd /d "%~dp0"
echo Final build completed!
echo.

REM Step: Rename win-unpacked to N.E.K.O
echo Renaming win-unpacked to N.E.K.O...
set "DIST_DIR=%TARGET_DIR%\dist"
if exist "%DIST_DIR%\win-unpacked" (
    if exist "%DIST_DIR%\N.E.K.O" (
        echo Removing old N.E.K.O folder...
        rmdir /s /q "%DIST_DIR%\N.E.K.O"
    )
    move "%DIST_DIR%\win-unpacked" "%DIST_DIR%\N.E.K.O"
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to rename win-unpacked to N.E.K.O!
        pause
        exit /b 1
    )
    echo Renamed successfully!
) else (
    echo [WARNING] win-unpacked folder not found, skipping rename.
)
echo.

REM Step: Sign final N.E.K.O.exe
echo Signing N.E.K.O.exe...
set AZURE_TENANT_ID=71451461-49ce-42e2-b4ae-a965434e3cf5
signtool.exe sign /v /fd SHA256 /tr "http://timestamp.acs.microsoft.com" /td "SHA256" /dlib "C:\Users\wehos\AppData\Local\Microsoft\MicrosoftTrustedSigningClientTools\Azure.CodeSigning.Dlib.dll" /dmdf ".\metadata.package.json" "%TARGET_DIR%\dist\N.E.K.O\N.E.K.O.exe"

echo.
echo ====================================
echo All steps completed successfully!
echo ====================================
echo.
echo Final package location: %TARGET_DIR%\dist\N.E.K.O
echo.

:final_done
pause
