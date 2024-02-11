#!/bin/bash --utf8

export PYTHONIOENCODING=utf8

trap 'kill $(jobs -pr)' SIGINT SIGTERM EXIT

if [[ $OSTYPE == "msys" ]]; then
  VENV_PATH=venv/Scripts/activate
else
  VENV_PATH=venv/bin/activate
fi

if [ ! -d "venv" ]; then
  if [ -x "$(command -v py)" ]; then
    py -3 -m venv venv
  else
    python3 -m venv venv
  fi

  if [ ! -d "venv" ]; then
    echo "The venv folder was not created! Please check if you have installed Python correctly (and that it is configured in the PATH/env)"
    sleep 45
    exit 1
  fi

  source $VENV_PATH
  pip install -r requirements.txt
else
  source $VENV_PATH
fi

echo "Starting bot (Ensure that it is online)..."

#mkdir -p ./.logs

#touch "./.logs/run.log"

python main.py #2>&1 | tee ./.logs/run.log

sleep 120s
