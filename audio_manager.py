"""audio_manager.py — Música de fondo con transiciones suaves y efectos de sonido.

`pygame.mixer.music.fadeout()` BLOQUEA el programa mientras dura el fundido.
Para no congelar la ventana, aquí el fundido se hace con una pequeña máquina
de estados que sube/baja el volumen un poco en cada fotograma (`update`).

Música:
  * Si la nueva escena usa la MISMA pista que ya suena, no se toca (sólo se
    ajusta su volumen, si cambió).
  * Si cambia la pista: fundido de salida -> carga de la nueva -> fundido de entrada.
  * `volume` por escena (0..1) multiplica el volumen general de música.

Efectos (SFX): se cargan una vez, se reproducen sobre la música y tienen su
propio volumen general.

Si no hay dispositivo de audio o falta un archivo, el juego sigue sin sonido.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional

import pygame

log = logging.getLogger(__name__)

_IDLE = "idle"
_FADING_IN = "fading_in"
_FADING_OUT = "fading_out"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class AudioManager:
    def __init__(
        self,
        base_dir: Path,
        music_volume: float = 0.6,
        sfx_volume: float = 0.8,
        fade_ms: int = 800,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._music_master = _clamp(music_volume)   # volumen general de música
        self._sfx_master = _clamp(sfx_volume)       # volumen general de efectos
        self._fade_ms = max(1, int(fade_ms))        # duración de cada fundido
        self._level = 0.0                           # nivel del fundido (0..1)
        self._scene_mult = 1.0                      # volumen de la escena en curso
        self._state = _IDLE
        self._playing: Optional[Path] = None        # pista cargada ahora mismo
        self._wanted: Optional[Path] = None         # pista que debe sonar
        self._wanted_mult = 1.0
        self._sounds: Dict[Path, Optional[pygame.mixer.Sound]] = {}
        self.enabled = self._init_mixer()

    # -- Música ------------------------------------------------------------ #
    @property
    def current_track(self) -> Optional[Path]:
        return self._playing

    @property
    def is_idle(self) -> bool:
        """True si no hay ningún fundido en marcha."""
        return self._state == _IDLE

    def _resolve(self, rel_path: str) -> Path:
        """Ruta absoluta y normalizada (así 'a/../b.wav' y 'b.wav' son la misma pista)."""
        return Path(os.path.normpath(self._base_dir / rel_path))

    def play(self, rel_path: Optional[str], volume: float = 1.0) -> None:
        """Pide que suene `rel_path` (relativa a la raíz). None = silencio."""
        if not self.enabled:
            return
        wanted = self._resolve(rel_path) if rel_path else None
        self._wanted_mult = _clamp(volume)
        if wanted == self._wanted:
            if wanted is not None and wanted == self._playing:
                self._scene_mult = self._wanted_mult  # misma pista: sólo cambia el volumen
                self._apply_volume()
            return

        self._wanted = wanted
        if self._playing is None:                    # silencio -> arrancar directo
            if wanted is not None:
                self._start(wanted)
        elif wanted == self._playing:                # volvemos a la pista que se apagaba
            self._scene_mult = self._wanted_mult
            self._state = _FADING_IN
        else:                                        # hay otra sonando -> fundido de salida
            self._state = _FADING_OUT

    def stop(self) -> None:
        """Apaga la música con un fundido."""
        self.play(None)

    def set_music_volume(self, volume: float) -> None:
        self._music_master = _clamp(volume)
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

    # -- Efectos ----------------------------------------------------------- #
    def play_sfx(self, rel_path: Optional[str]) -> None:
        """Reproduce un efecto una vez, por encima de la música."""
        if not self.enabled or not rel_path:
            return
        path = self._resolve(rel_path)
        if path not in self._sounds:
            try:
                self._sounds[path] = pygame.mixer.Sound(str(path))
            except (pygame.error, FileNotFoundError) as exc:
                log.warning("No se pudo cargar el efecto '%s': %s", path, exc)
                self._sounds[path] = None
        sound = self._sounds[path]
        if sound is not None:
            sound.set_volume(self._sfx_master)
            sound.play()

    def set_sfx_volume(self, volume: float) -> None:
        self._sfx_master = _clamp(volume)

    # -- Ciclo de vida ----------------------------------------------------- #
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
        self._scene_mult = self._wanted_mult
        self._state = _FADING_IN

    def _apply_volume(self) -> None:
        if self.enabled and pygame.mixer.get_init():
            pygame.mixer.music.set_volume(self._music_master * self._scene_mult * self._level)
