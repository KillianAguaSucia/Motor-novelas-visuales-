"""scene.py — Modelo de datos del árbol de escenas.

Este módulo NO depende de Pygame: sólo describe y valida la historia que se
carga desde el JSON. Así puede probarse o reutilizarse (por ejemplo, en un
editor de historias) sin abrir una ventana.

Estructura:
    Story  ──contiene──▶  Scene  ──contiene──▶  Choice (botón → escena destino)
                           ├── TextStyle        (color, tamaño, tipografía)
                           └── BackgroundStyle  (imagen, posición, escala)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

Color = Tuple[int, int, int]

# Puntos de anclaje admitidos (coinciden con los atributos de pygame.Rect).
VALID_ANCHORS = frozenset(
    {
        "topleft", "midtop", "topright",
        "midleft", "center", "midright",
        "bottomleft", "midbottom", "bottomright",
    }
)
# Modos de ajuste del fondo:
#   cover   -> rellena la ventana completa (recorta lo que sobre)
#   contain -> se ve entera dentro de la ventana (pueden quedar bandas)
#   none    -> tamaño nativo (escalado sólo según el tamaño de la ventana)
VALID_FITS = frozenset({"cover", "contain", "none"})

DEFAULT_TEXT_COLOR: Color = (245, 240, 225)
DEFAULT_TEXT_SIZE = 28
DEFAULT_FILL: Color = (0, 0, 0)


class StoryError(Exception):
    """Error de formato o de coherencia en el archivo de la historia."""


# --------------------------------------------------------------------------- #
# Utilidades de parseo
# --------------------------------------------------------------------------- #
def parse_color(value: Any, default: Color) -> Color:
    """Convierte '#RRGGBB', '#RGB' o [r, g, b] en una tupla (r, g, b)."""
    if value is None:
        return default
    try:
        if isinstance(value, str):
            hex_value = value.strip().lstrip("#")
            if len(hex_value) == 3:
                hex_value = "".join(ch * 2 for ch in hex_value)
            if len(hex_value) != 6:
                raise ValueError
            return tuple(int(hex_value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            channels = tuple(int(c) for c in value[:3])
            if all(0 <= c <= 255 for c in channels):
                return channels  # type: ignore[return-value]
    except ValueError:
        pass
    raise StoryError(f"Color inválido: {value!r} (usa '#RRGGBB' o [r, g, b])")


def _optional_number(data: Dict[str, Any], key: str) -> Optional[float]:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StoryError(f"'{key}' debe ser un número, no {value!r}")
    return float(value)


# --------------------------------------------------------------------------- #
# Estilos
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TextStyle:
    """Estilo tipográfico del texto de una escena.

    font:  ruta a un .ttf/.otf (relativa a la raíz del proyecto), nombre de una
           fuente del sistema ('arial', 'georgia'...) o None para la de Pygame.
    size:  tamaño en píxeles para la resolución de diseño (1280x720); el motor
           lo escala automáticamente si la ventana cambia de tamaño.
    """

    color: Color = DEFAULT_TEXT_COLOR
    size: int = DEFAULT_TEXT_SIZE
    font: Optional[str] = None

    @classmethod
    def from_dict(
        cls, data: Optional[Dict[str, Any]], base: Optional["TextStyle"] = None
    ) -> "TextStyle":
        """Crea un estilo; las claves ausentes se heredan de `base`."""
        base = base or cls()
        data = data or {}
        size = data.get("size", base.size)
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise StoryError(f"'size' debe ser un entero positivo, no {size!r}")
        return cls(
            color=parse_color(data.get("color"), base.color),
            size=size,
            font=data.get("font", base.font),
        )


@dataclass(frozen=True)
class BackgroundStyle:
    """Imagen de fondo y cómo colocarla.

    x, y:    punto de la pantalla donde se coloca el `anchor` de la imagen,
             en coordenadas de la resolución de diseño (1280x720).
             Si falta alguno, la imagen se centra en ese eje.
    scale:   multiplicador de tamaño (1.0 = sin cambios, 1.5 = 150 %...).
    fill:    color que se ve donde la imagen no llega a cubrir la pantalla.
    """

    image: Optional[str] = None
    fit: str = "cover"
    scale: float = 1.0
    x: Optional[float] = None
    y: Optional[float] = None
    anchor: str = "center"
    fill: Color = DEFAULT_FILL

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "BackgroundStyle":
        data = data or {}
        fit = data.get("fit", "cover")
        if fit not in VALID_FITS:
            raise StoryError(f"'fit' debe ser uno de {sorted(VALID_FITS)}, no {fit!r}")
        anchor = data.get("anchor", "center")
        if anchor not in VALID_ANCHORS:
            raise StoryError(f"'anchor' debe ser uno de {sorted(VALID_ANCHORS)}, no {anchor!r}")
        scale = _optional_number(data, "scale")
        if scale is None:
            scale = 1.0
        if scale <= 0:
            raise StoryError(f"'scale' debe ser mayor que 0, no {scale!r}")
        return cls(
            image=data.get("image"),
            fit=fit,
            scale=scale,
            x=_optional_number(data, "x"),
            y=_optional_number(data, "y"),
            anchor=anchor,
            fill=parse_color(data.get("fill"), DEFAULT_FILL),
        )


# --------------------------------------------------------------------------- #
# Nodos del árbol
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Choice:
    """Una rama: texto del botón + ID de la escena a la que lleva."""

    text: str
    target: str


@dataclass(frozen=True)
class Scene:
    """Un nodo del árbol de escenas."""

    id: str
    text: str
    text_style: TextStyle
    background: BackgroundStyle
    music: Optional[str]
    choices: Tuple[Choice, ...]

    @property
    def is_ending(self) -> bool:
        """Una escena sin opciones es un final."""
        return not self.choices

    @classmethod
    def from_dict(cls, data: Dict[str, Any], default_style: TextStyle) -> "Scene":
        scene_id = data.get("id")
        if not isinstance(scene_id, str) or not scene_id.strip():
            raise StoryError(f"Cada escena necesita un 'id' de texto no vacío: {data!r}")
        try:
            choices = tuple(
                Choice(text=str(c["text"]), target=str(c["target"]))
                for c in data.get("choices", [])
            )
            return cls(
                id=scene_id,
                text=str(data.get("text", "")),
                text_style=TextStyle.from_dict(data.get("text_style"), default_style),
                background=BackgroundStyle.from_dict(data.get("background")),
                music=data.get("music"),
                choices=choices,
            )
        except KeyError as exc:
            raise StoryError(f"Escena '{scene_id}': falta la clave {exc} en una opción") from exc
        except StoryError as exc:
            raise StoryError(f"Escena '{scene_id}': {exc}") from exc


class Story:
    """Árbol completo de escenas, indexado por ID."""

    def __init__(
        self, scenes: Iterable[Scene], start_id: str, settings: Dict[str, Any]
    ) -> None:
        self.scenes: Dict[str, Scene] = {}
        for scene in scenes:
            if scene.id in self.scenes:
                raise StoryError(f"ID de escena duplicado: '{scene.id}'")
            self.scenes[scene.id] = scene
        self.start_id = start_id
        self.settings = settings
        self._validate()

    # -- Carga ------------------------------------------------------------- #
    @classmethod
    def load(cls, path: Path) -> "Story":
        """Lee y valida un archivo JSON de historia."""
        path = Path(path)
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError as exc:
            raise StoryError(f"No se encontró el archivo de historia: {path}") from exc
        except json.JSONDecodeError as exc:
            raise StoryError(
                f"JSON inválido en {path.name} (línea {exc.lineno}, columna {exc.colno}): {exc.msg}"
            ) from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Story":
        raw_scenes = data.get("scenes")
        if not isinstance(raw_scenes, list) or not raw_scenes:
            raise StoryError("El JSON debe incluir una lista 'scenes' con al menos una escena")
        settings = data.get("settings", {})
        try:
            default_style = TextStyle.from_dict(settings.get("default_text_style"))
        except StoryError as exc:
            raise StoryError(f"settings.default_text_style: {exc}") from exc
        scenes = [Scene.from_dict(raw, default_style) for raw in raw_scenes]
        start_id = data.get("start") or scenes[0].id
        return cls(scenes, start_id, settings)

    # -- Acceso ------------------------------------------------------------ #
    def get(self, scene_id: str) -> Scene:
        try:
            return self.scenes[scene_id]
        except KeyError as exc:
            raise StoryError(f"La escena '{scene_id}' no existe") from exc

    @property
    def start(self) -> Scene:
        return self.get(self.start_id)

    # -- Validación -------------------------------------------------------- #
    def _validate(self) -> None:
        """Comprueba que la escena inicial y todos los destinos existan."""
        if self.start_id not in self.scenes:
            raise StoryError(f"La escena inicial '{self.start_id}' no existe")
        for scene in self.scenes.values():
            for choice in scene.choices:
                if choice.target not in self.scenes:
                    raise StoryError(
                        f"Escena '{scene.id}': la opción '{choice.text}' "
                        f"apunta a una escena inexistente ('{choice.target}')"
                    )