#!/bin/bash
# Foundry - double-click to sync and build every public repo into ~/GitHub.
cd "$(dirname "$0")" || exit 1

PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
         python3; do
  if command -v "$c" >/dev/null 2>&1; then
    # Apple's /usr/bin/python3 is a stub until the Command Line Tools exist - do not call it blind
    if [ "$(command -v "$c")" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
      continue
    fi
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
      PY="$c"; break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo 'No Python 3 found. Installing the Apple Command Line Tools, which include python3.'
  echo 'Accept the prompt, wait for it to finish, then double-click this file again.'
  xcode-select --install
  read -r -p 'Press Return to close.'
  exit 1
fi

"$PY" ./foundry.py "$@"
echo
read -r -p 'Done. Press Return to close.'
