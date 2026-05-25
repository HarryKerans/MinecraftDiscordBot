#!/bin/bash
set -e

echo "Starting Minecraft Discord Bot..."
exec uv run python app/serverbot.py
