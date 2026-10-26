@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==========================================
echo   一键推送到 GitHub
echo ==========================================
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo [错误] 当前目录不是 git 仓库
  pause
  exit /b 1
)

git add -A

git diff --cached --quiet
if not errorlevel 1 (
  echo [提示] 没有需要提交的改动
  echo.
  git status -sb
  pause
  exit /b 0
)

echo 将要提交的文件：
git diff --cached --name-status
echo.

echo 大文件提醒（超过 10MB，确认是否需要推送）：
for /f "tokens=*" %%f in ('git diff --cached --name-only') do (
  if exist "%%f" (
    for %%s in ("%%f") do (
      if %%~zs GTR 10485760 echo   [大文件] %%f  %%~zs 字节
    )
  )
)
echo.

set "msg="
set /p "msg=提交说明（直接回车用时间戳）: "
if "!msg!"=="" (
  set "msg=chore: sync %date% %time%"
)

git commit -m "!msg!"
if errorlevel 1 (
  echo [错误] 提交失败，已中止
  pause
  exit /b 1
)

echo.
echo 正在推送...
git push origin main
if errorlevel 1 (
  echo.
  echo [错误] 推送失败。常见原因：网络不通、凭据过期、远端有新提交需要先 pull
  pause
  exit /b 1
)

echo.
echo [完成] 已推送到 GitHub
pause
