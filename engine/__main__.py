"""Allow `python -m engine ...`."""
import sys

from .console import configure_utf8_stdio
from .cli import main

configure_utf8_stdio()
sys.exit(main())
