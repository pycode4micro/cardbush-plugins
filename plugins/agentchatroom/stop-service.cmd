@echo off
node "%~dp0runtime\cli.mjs" stop
if errorlevel 1 pause
