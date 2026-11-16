"""支持 `python -m ai_team`。"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
