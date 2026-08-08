"""
Kill Switch Remote Script
==========================
Creates the KILL file to trigger graceful shutdown.
Run: python kill.py
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timezone


def main():
    kill_file = Path("KILL")

    if kill_file.exists():
        print(f"KILL file already exists at: {kill_file.absolute()}")
        print("System should already be shutting down.")
        return

    # Create KILL file
    kill_file.write_text(
        f"Kill switch triggered at {datetime.now(timezone.utc).isoformat()}\n"
        f"Triggered by: kill.py\n"
    )

    print(f"KILL file created at: {kill_file.absolute()}")
    print("System will shut down gracefully on next cycle (max 1 second).")
    print("To cancel: delete the KILL file before the system reads it.")


if __name__ == "__main__":
    main()
