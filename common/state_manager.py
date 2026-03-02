import os
import json
from pathlib import Path
import logging

from common.config_loader import settings

logger = logging.getLogger("GloBot_StateManager")

DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}"))
HISTORY_FILE = DATA_DIR / "history.json"
DYN_MAP_FILE = DATA_DIR / "dyn_map.json"

def load_history():
    if not HISTORY_FILE.exists(): return set()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f: return set(json.load(f))
    except: return set()

def save_history(history_set):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(history_set), f, ensure_ascii=False, indent=2)

def load_dyn_map():
    if not DYN_MAP_FILE.exists(): return {}
    try:
        with open(DYN_MAP_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

def save_dyn_map(dyn_map):
    DYN_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DYN_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(dyn_map, f, ensure_ascii=False, indent=2)