#!/usr/bin/env bash
# Build script for Render deployment
set -o errexit

pip install --upgrade pip
pip install -r backend/requirements.txt
