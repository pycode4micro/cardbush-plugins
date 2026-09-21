@echo off
node "%~dp0runtime\cli.mjs" start
if errorlevel 1 pause
