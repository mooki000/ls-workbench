#!/bin/sh
# stands in for hs-run.sh: <copy> <tag> <seconds> [--user <save>] [NAME=VALUE ...]; the NAME=VALUE words become the game's environment
# (the driver finds the game by the HOS_CONTROL in its environment)
for a in "$@"; do
    case "$a" in
        [A-Z]*=*) export "$a" ;;
    esac
done
exec python3 -I -B "$(dirname "$0")/fake_game.py" "$@"
