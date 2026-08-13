#!/usr/bin/env bash
# Verify frontend build outputs that are required by packaged desktop builds.
set -euo pipefail

for model in yui-lolita yui-origin; do
  required_model_file="static/$model/$model.model3.json"
  if [ ! -s "$required_model_file" ]; then
    echo "ERROR: missing or empty $required_model_file after build_frontend.sh" >&2
    exit 1
  fi
done

for model in yui-lolita yui-origin yui-sister; do
  required_pngtuber_file="static/pngtuber/$model/model.json"
  if [ ! -s "$required_pngtuber_file" ]; then
    echo "ERROR: missing or empty $required_pngtuber_file after build_frontend.sh" >&2
    exit 1
  fi
done
