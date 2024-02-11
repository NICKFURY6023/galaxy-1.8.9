#!/bin/bash

export PYTHONIOENCODING=utf8

trap 'kill $(jobs -pr)' SIGINT SIGTERM EXIT

if [ ! -d ".git" ] || [ -z "$(git remote -v)" ]; then
  git init
  git remote add origin https://github.com/SeasonPhan/MuseHeart-MusicBot-EN.git
  git fetch origin
  git checkout -b main -f --track origin/main
else
  git reset --hard
  git pull --allow-unrelated-histories -X theirs
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
fi

if [[ $OSTYPE == "msys" ]]; then
  VENV_PATH=venv/Scripts/activate
else
  VENV_PATH=venv/bin/activate
fi

source $VENV_PATH

touch "./.logs/update.log"

if [ ! -f "./venv/requirements.txt" ] || ! cmp --silent -- "./requirements.txt" "./venv/requirements.txt"; then
  pip install -r requirements.txt --no-cache-dir 2>&1 | tee "./.logs/update.log"
  cp -r requirements.txt ./venv/requirements.txt
fi

sleep 30s
