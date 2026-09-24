"""Double-click entry point (desktop executable and the `devicescout-app` launcher).

Starts the web app on a free local port and opens the browser. With arguments it
behaves exactly like the `devicescout` command line.
"""

from __future__ import annotations

import socket
import sys


def _free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def main() -> None:
    # Absolute import: PyInstaller runs this file as a top-level script, not as part of the package.
    from devicescout.cli import main as cli_main

    args = sys.argv[1:]
    if not args:
        args = ["serve", "--port", str(_free_port())]
    cli_main(args)


if __name__ == "__main__":
    main()
