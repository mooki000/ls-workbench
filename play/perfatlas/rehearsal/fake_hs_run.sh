#!/bin/sh
# stands in for hs-run.sh: <copy> <tag> <seconds> [--user <save>] [NAME=VALUE ...]
exec python3 -I -B "$(dirname "$0")/fake_game.py" "$@"
