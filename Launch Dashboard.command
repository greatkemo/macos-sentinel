#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'Python environment missing. Install Python 3.13+, then follow README setup:'
  print '  brew install python@3.13'
  print '  python3.13 -m venv .venv && source .venv/bin/activate'
  print '  python -m pip install --upgrade pip'
  print '  python -m pip install -r requirements.txt'
  print 'See README Install troubleshooting for pip / requirements errors.'
  read '?Press Return to close.'
  exit 1
fi
.venv/bin/python -B desktop/launch.py
if (( $? != 0 )); then
  read '?Press Return to close.'
fi
