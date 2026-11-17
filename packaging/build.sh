#!/usr/bin/env bash
# builds a single file app into dist/
# usage: bash packaging/build.sh
set -e
cd "$(dirname "$0")/.."
pip install . pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --name MapMerger \
    --exclude-module tkinter \
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets \
    --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.Qt3DCore \
    --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtCharts \
    --exclude-module PySide6.QtDataVisualization --exclude-module PySide6.QtPdf \
    packaging/app.py
