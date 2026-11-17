# entry point for the packaged app (pyinstaller)
import sys

from map_merger.gui import main

if __name__ == "__main__":
    sys.exit(main())
