"""errors.py — Excepciones compartidas por todos los módulos."""
from __future__ import annotations


class StoryError(Exception):
    """Error de formato o de coherencia en el archivo de la historia."""
