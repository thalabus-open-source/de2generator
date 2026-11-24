"""Filesystem locations the HTTP layer serves from."""

import os

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # …/de2generator/src
STATIC_DIR = os.path.join(SRC_DIR, "static")
LOGS_DIR = os.path.join(os.getcwd(), "logs")
