#!/bin/sh
cd /home/raymondli/Agents/arxiv-summarizer || exit 1
set -a
. ./.env
set +a
exec ./.venv/bin/python arxiv_summarizer.py
