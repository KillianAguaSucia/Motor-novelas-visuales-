"""save_manager.py — Partidas guardadas y preferencias del jugador (sin Pygame).

Archivos (JSON legible, escritura atómica para no corromperlos si el programa
se cierra a mitad de guardar):

    saves/settings.json        preferencias: volúmenes y velocidad del texto
    saves/<historia>/slot1.json ... slotN.json, auto.json
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

log = logging.getLogger(__name__)

SAVE_VERSION = 1
Slot = Union[int, str]
AUTO: Slot = "auto"


class SaveError(Exception):
    """No se pudo escribir un archivo de guardado."""


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    """Escribe en un temporal y lo renombra: nunca deja un JSON a medias."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        raise SaveError(f"No se pudo escribir {path}: {exc}") from exc


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Lee un JSON; si falta o está corrupto devuelve None (y avisa en el log)."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Archivo ilegible '%s': %s", path, exc)
        return None
    if not isinstance(data, dict):
        log.warning("Archivo con formato inesperado: '%s'", path)
        return None
    return data


# --------------------------------------------------------------------------- #
# Partidas
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SaveData:
    """Instantánea de una partida: dónde está y qué variables lleva."""

    scene_id: str
    line_index: int
    variables: Dict[str, Any]
    saved_at: str  # ISO 8601

    @property
    def when(self) -> str:
        """Fecha corta para mostrar en pantalla ('13/10 23:40')."""
        try:
            return datetime.fromisoformat(self.saved_at).strftime("%d/%m %H:%M")
        except ValueError:
            return "?"


class SaveManager:
    """Ranuras numeradas (1..slots) más una de autoguardado."""

    def __init__(self, directory: Path, slots: int = 3) -> None:
        self.directory = Path(directory)
        self.slot_ids: List[int] = list(range(1, slots + 1))

    def _path(self, slot: Slot) -> Path:
        return self.directory / (f"{slot}.json" if slot == AUTO else f"slot{slot}.json")

    def save(self, slot: Slot, scene_id: str, line_index: int, variables: Dict[str, Any]) -> SaveData:
        data = SaveData(
            scene_id=scene_id,
            line_index=line_index,
            variables=dict(variables),
            saved_at=datetime.now().astimezone().isoformat(timespec="milliseconds"),
        )
        _atomic_write(self._path(slot), {"version": SAVE_VERSION, **asdict(data)})
        return data

    def read(self, slot: Slot) -> Optional[SaveData]:
        raw = _read_json(self._path(slot))
        if raw is None:
            return None
        try:
            if raw.get("version") != SAVE_VERSION:
                raise ValueError(f"versión {raw.get('version')!r} no soportada")
            variables = raw["variables"]
            if not isinstance(variables, dict):
                raise ValueError("'variables' no es un objeto")
            return SaveData(
                scene_id=str(raw["scene_id"]),
                line_index=int(raw["line_index"]),
                variables=variables,
                saved_at=str(raw.get("saved_at", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Partida guardada inválida (%s): %s", self._path(slot).name, exc)
            return None

    def latest(self) -> Optional[Tuple[Slot, SaveData]]:
        """La partida más reciente entre todas las ranuras (para 'Continuar')."""
        candidates = []
        for slot in (*self.slot_ids, AUTO):
            data = self.read(slot)
            if data is not None:
                candidates.append((data.saved_at, slot, data))
        if not candidates:
            return None
        _, slot, data = max(candidates, key=lambda item: item[0])
        return slot, data


# --------------------------------------------------------------------------- #
# Preferencias
# --------------------------------------------------------------------------- #
MIN_CPS, MAX_CPS = 10.0, 200.0


@dataclass
class UserSettings:
    """Preferencias del jugador. Todos los valores van de 0.0 a 1.0."""

    music_volume: float = 0.6
    sfx_volume: float = 0.8
    text_speed: float = 0.3  # 1.0 = texto instantáneo

    @property
    def typing_cps(self) -> float:
        """Caracteres por segundo de la máquina de escribir (0 = instantáneo)."""
        if self.text_speed >= 0.995:
            return 0.0
        return MIN_CPS + self.text_speed * (MAX_CPS - MIN_CPS)

    @classmethod
    def load(cls, path: Path) -> "UserSettings":
        settings = cls()
        raw = _read_json(Path(path)) or {}
        for name in ("music_volume", "sfx_volume", "text_speed"):
            value = raw.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(settings, name, max(0.0, min(1.0, float(value))))
        return settings

    def save(self, path: Path) -> None:
        _atomic_write(Path(path), asdict(self))
