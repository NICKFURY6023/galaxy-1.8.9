#!/bin/bash

export PYTHONIOENCODING=utf8

if  [ ! -d ".git" ] || [ -z "$(git remote -v)" ]; then

  git init

  if [ -z "$SOURCE_REPO" ]; then
    git remote add origin https://github.com/NICK-FURY-6023/galaxy-1.8.9
  else
    git remote add origin $SOURCE_REPO
  fi
  git fetch origin
  git checkout -b main -f --track origin/main
else
  git reset --hard
  git pull --allow-unrelated-histories -X theirs
fi
