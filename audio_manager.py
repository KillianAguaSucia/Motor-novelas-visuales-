"""audio_manager.py — Música de fondo con transiciones suaves.

`pygame.mixer.music.fadeout()` BLOQUEA el programa mientras dura el fundido.
Para no congelar la ventana, aquí el fundido se hace con una pequeña máquina
de estados que sube/baja el volumen un poco en cada fotograma (`update`).

Comportamiento:
  * Si la nueva escena usa la MISMA pista que ya suena, la música no se toca
    (no se reinicia ni se corta).
  * Si cambia la pista: fundido de salida -> carga de la nueva -> fundido de entrada.
  * Si no hay dispositivo de audio o falta un archivo, el juego sigue sin sonido.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pygame

log = logging.getLogger(__name__)

_IDLE = "idle"
_FADING_IN = "fading_in"
_FADING_OUT = "fading_out"


class AudioManager:
    def __init__(self, base_dir: Path, volume: float = 0.6, fade_ms: int = 800) -> None:
        self._base_dir = Path(base_dir)
        self._master = max(0.0, min(1.0, volume))   # volumen general (0..1)
        self._fade_ms = max(1, int(fade_ms))         # duración de cada fundido
        self._level = 0.0                            # volumen del fundido (0..1)
        self._state = _IDLE
        self._playing: Optional[Path] = None         # pista cargada ahora mismo
        self._wanted: Optional[Path] = None          # pista que debe sonar
        self.enabled = self._init_mixer()

    # -- API pública ------------------------------------------------------- #
    @property
    def current_track(self) -> Optional[Path]:
        return self._playing

    def play(self, rel_path: Optional[str]) -> None:
        """Pide que suene `rel_path` (relativa a la raíz). None = silencio."""
        if not self.enabled:
            return
        wanted = self._base_dir / rel_path if rel_path else None
        if wanted == self._wanted:
            return  # misma pista: no interrumpir

        self._wanted = wanted
        if self._playing is None:                    # silencio -> arrancar directo
            if wanted is not None:
                self._start(wanted)
        elif wanted == self._playing:                # volvemos a la pista que se apagaba
            self._state = _FADING_IN
        else:                                        # hay otra sonando -> fundido de salida
            self._state = _FADING_OUT

    def stop(self) -> None:
        """Apaga la música con un fundido."""
        self.play(None)

    def set_volume(self, volume: float) -> None:
        self._master = max(0.0, min(1.0, volume))
        self._apply_volume()

    def update(self, dt_ms: float) -> None:
        """Avanza el fundido. Llamar una vez por fotograma con el delta en ms."""
        if not self.enabled or self._state == _IDLE:
            return
        step = dt_ms / self._fade_ms
        if self._state == _FADING_IN:
            self._level = min(1.0, self._level + step)
            if self._level >= 1.0:
                self._state = _IDLE
        else:  # _FADING_OUT
            self._level = max(0.0, self._level - step)
            if self._level <= 0.0:
                pygame.mixer.music.stop()
                self._playing = None
                self._state = _IDLE
                if self._wanted is not None:
                    self._start(self._wanted)
                return
        self._apply_volume()

    def shutdown(self) -> None:
        if self.enabled and pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        self.enabled = False

    # -- Internos ---------------------------------------------------------- #
    @staticmethod
    def _init_mixer() -> bool:
        if pygame.mixer.get_init():
            return True
        try:
            pygame.mixer.init()
            return True
        except pygame.error as exc:
            log.warning("Audio desactivado (no se pudo iniciar el mixer): %s", exc)
            return False

    def _start(self, path: Path) -> None:
        """Carga `path` en bucle, empezando en silencio para el fundido de entrada."""
        try:
            pygame.mixer.music.load(str(path))
            self._level = 0.0
            pygame.mixer.music.set_volume(0.0)
            pygame.mixer.music.play(loops=-1)
        except (pygame.error, FileNotFoundError) as exc:
            log.warning("No se pudo reproducir '%s': %s", path, exc)
            self._playing = None
            self._state = _IDLE
            return
        self._playing = path
        self._state = _FADING_IN

    def _apply_volume(self) -> None:
        if self.enabled and pygame.mixer.get_init():
            pygame.mixer.music.set_volume(self._master * self._level)