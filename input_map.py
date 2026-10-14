"""input_map.py — Traduce eventos de Pygame (teclado, mando) a acciones del juego.

Los estados del juego no conocen teclas concretas: sólo reciben acciones
(CONFIRM, BACK, UP...). Así añadir un mando o cambiar teclas es cambiar este archivo.
El ratón se trata aparte en cada estado, porque depende de la posición en pantalla.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, Optional

import pygame


class Action(Enum):
    CONFIRM = auto()      # avanzar texto / aceptar
    BACK = auto()         # pausa / volver
    UP = auto()
    DOWN = auto()
    LEFT = auto()
    RIGHT = auto()
    NUMBER = auto()       # atajo 1..9 para elegir opción (ver Input.index)
    QUICKSAVE = auto()
    QUICKLOAD = auto()
    FULLSCREEN = auto()


@dataclass(frozen=True)
class Input:
    action: Action
    index: int = -1  # sólo para NUMBER: 0..8


_KEYS: Dict[int, Action] = {
    pygame.K_RETURN: Action.CONFIRM,
    pygame.K_KP_ENTER: Action.CONFIRM,
    pygame.K_SPACE: Action.CONFIRM,
    pygame.K_ESCAPE: Action.BACK,
    pygame.K_UP: Action.UP,
    pygame.K_w: Action.UP,
    pygame.K_DOWN: Action.DOWN,
    pygame.K_s: Action.DOWN,
    pygame.K_LEFT: Action.LEFT,
    pygame.K_a: Action.LEFT,
    pygame.K_RIGHT: Action.RIGHT,
    pygame.K_d: Action.RIGHT,
    pygame.K_F5: Action.QUICKSAVE,
    pygame.K_F9: Action.QUICKLOAD,
    pygame.K_F11: Action.FULLSCREEN,
}
_NUMBER_KEYS: Dict[int, int] = {
    **{pygame.K_1 + i: i for i in range(9)},
    **{pygame.K_KP1 + i: i for i in range(9)},
}

# Mando genérico (Xbox/PlayStation en SDL): A/cruz = aceptar, B/círculo = atrás, Start = atrás.
JOY_CONFIRM_BUTTONS = frozenset({0})
JOY_BACK_BUTTONS = frozenset({1, 7})


def translate(event: pygame.event.Event) -> Optional[Input]:
    """Devuelve la acción asociada a `event`, o None si no nos interesa."""
    if event.type == pygame.KEYDOWN:
        if event.key in _NUMBER_KEYS:
            return Input(Action.NUMBER, _NUMBER_KEYS[event.key])
        action = _KEYS.get(event.key)
        return Input(action) if action else None

    if event.type == pygame.JOYHATMOTION:
        x, y = event.value
        if y > 0:
            return Input(Action.UP)
        if y < 0:
            return Input(Action.DOWN)
        if x < 0:
            return Input(Action.LEFT)
        if x > 0:
            return Input(Action.RIGHT)
        return None

    if event.type == pygame.JOYBUTTONDOWN:
        if event.button in JOY_CONFIRM_BUTTONS:
            return Input(Action.CONFIRM)
        if event.button in JOY_BACK_BUTTONS:
            return Input(Action.BACK)
    return None
