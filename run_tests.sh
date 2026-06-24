#!/usr/bin/env bash
set -e
python -m unittest test_conversation_logic.py
python -m unittest test_api_logic.py
