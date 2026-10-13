"""main.py — Punto de entrada.

Uso:
    python main.py                      # carga data/story.json
    python main.py ruta/otra_historia.json
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from engine import Engine
from scene import StoryError

BASE_DIR = Path(__file__).resolve().parent          # raíz del proyecto
DEFAULT_STORY = BASE_DIR / "data" / "story.json"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    story_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STORY
    try:
        Engine(story_path, BASE_DIR).run()
    except StoryError as exc:
        print(f"Error en la historia: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())