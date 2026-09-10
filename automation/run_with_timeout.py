from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a command with a hard timeout and stream captured output on completion.")
    parser.add_argument("--timeout", type=int, required=True, help="Timeout in seconds")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("command is required")
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=args.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        if output:
            sys.stdout.write(output)
        print(f"TIMEOUT after {args.timeout}s: {' '.join(command)}", flush=True)
        return 124
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
