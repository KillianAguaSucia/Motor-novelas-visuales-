"""main.py — Punto de entrada.

Uso:
    python main.py                        # carga data/story.json
    python main.py otra_historia.json
    python main.py --check                # valida historia y recursos sin abrir ventana
    python main.py --strict               # no arranca si falta algún archivo
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from scene import Story, StoryError

BASE_DIR = Path(__file__).resolve().parent          # raíz del proyecto
DEFAULT_STORY = BASE_DIR / "data" / "story.json"
log = logging.getLogger("novela")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Motor de novela visual con Pygame")
    parser.add_argument("story", nargs="?", type=Path, default=DEFAULT_STORY,
                        help="archivo JSON de la historia (por defecto data/story.json)")
    parser.add_argument("--check", action="store_true",
                        help="valida la historia y los recursos y sale (sin ventana)")
    parser.add_argument("--strict", action="store_true",
                        help="aborta si falta algún archivo de imagen, sonido o fuente")
    parser.add_argument("--saves", type=Path, default=None,
                        help="carpeta de partidas guardadas (por defecto ./saves)")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    args = parse_args(argv)

    try:
        story = Story.load(args.story)
    except StoryError as exc:
        print(f"Error en la historia: {exc}", file=sys.stderr)
        return 1

    for warning in story.warnings:
        log.warning(warning)
    missing = story.missing_assets(BASE_DIR)
    for entry in missing:
        log.warning("Falta el archivo: %s", entry)

    if args.check:
        print(f"{len(story.scenes)} escenas, {len(story.warnings)} avisos, "
              f"{len(missing)} archivos que faltan.")
        return 1 if missing else 0
    if missing and args.strict:
        print("Faltan archivos (modo --strict).", file=sys.stderr)
        return 1

    from engine import Engine  # se importa aquí para que --check no necesite Pygame ni pantalla

    Engine(story, BASE_DIR, saves_dir=args.saves, story_name=args.story.stem).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
