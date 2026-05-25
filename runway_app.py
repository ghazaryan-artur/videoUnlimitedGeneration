"""PyInstaller entry script — bundled as RunwayAutomation.exe / RunwayAutomation.app.

Plain wrapper around app.ui.app.run() so the build artifact has a stable name
that matches the project (not "app").
"""
from app.ui.app import run

if __name__ == "__main__":
    run()
