#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'Python environment missing. Follow README setup instructions.'
  read '?Press Return to close.'
  exit 1
fi
.venv/bin/python -B desktop/launch.py
if (( $? != 0 )); then
  read '?Press Return to close.'
fi
