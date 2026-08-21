from __future__ import annotations

"""Compatibility module for the old import path.

The active Electron app imports file_tidier_core through file_tidier_backend.py.
The previous Tkinter UI is preserved in file_tidier_tk_legacy.py for reference only.
"""

from file_tidier_core import *  # noqa: F401,F403


if __name__ == "__main__":
    print("File Tidier now uses the Electron UI. Run FileTidier-Electron.vbs or electron-app/pnpm start.")
    print("Legacy Tkinter UI source is preserved in file_tidier_tk_legacy.py.")