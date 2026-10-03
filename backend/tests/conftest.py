"""让 pytest 从 backend 目录外运行时也能 import app 包。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
