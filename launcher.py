"""Entry point for the packaged app (PyInstaller): opens the app window."""
import sys

from gazehop.__main__ import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["--window"]))
