"""scene.py — Modelo de datos del árbol de escenas.

Este módulo NO depende de Pygame: sólo describe y valida la historia que se
carga desde el JSON. Así puede probarse (o reutilizarse en un editor) sin abrir
una ventana.

    Story ──▶ Scene ──┬─▶ DialogueLine(s)   texto por pasos, con hablante y estilo
                      ├─▶ Sprite(s)          personajes / objetos sobre el fondo
                      ├─▶ BackgroundStyle    imagen de fondo y su posición
                      └─▶ Choice(s)          botones → escena destino (con condiciones)
"""
from __future__ import annotations

import difflib
import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from errors import StoryError
from state import Condition, Effect, parse_effects, parse_requires

__all__ = [
    "StoryError", "TextStyle", "BackgroundStyle", "Sprite", "DialogueLine",
    "Choice", "Scene", "StorySettings", "Story", "parse_color",
]

Color = Tuple[int, int, int]
Warn = Callable[[str], None]

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
VALID_POSITIONS = frozenset({"left", "center", "right"})
VALID_IF_LOCKED = frozenset({"hide", "disable"})
FONT_SUFFIXES = (".ttf", ".otf")

DEFAULT_TEXT_COLOR: Color = (245, 240, 225)
DEFAULT_TEXT_SIZE = 28
DEFAULT_FILL: Color = (0, 0, 0)


def _is_font_file(name: Optional[str]) -> bool:
    """True si `name` es un archivo .ttf/.otf (y no el nombre de una fuente del sistema)."""
    return bool(name) and name.lower().endswith(FONT_SUFFIXES)  # type: ignore[union-attr]


def _ignore(_: str) -> None:
    """Receptor de avisos que no hace nada (valor por defecto)."""


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
            r, g, b = (int(hex_value[i:i + 2], 16) for i in (0, 2, 4))
            return (r, g, b)
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            r, g, b = (int(c) for c in value[:3])
            if all(0 <= c <= 255 for c in (r, g, b)):
                return (r, g, b)
    except ValueError:
        pass
    raise StoryError(f"Color inválido: {value!r} (usa '#RRGGBB' o [r, g, b])")


def _number(data: Dict[str, Any], key: str) -> Optional[float]:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StoryError(f"'{key}' debe ser un número, no {value!r}")
    return float(value)


def _check_keys(data: Dict[str, Any], allowed: Iterable[str], where: str, warn: Warn) -> None:
    """Avisa de claves desconocidas (típicas erratas) sugiriendo la más parecida."""
    allowed = list(allowed)
    for key in data:
        if key not in allowed:
            close = difflib.get_close_matches(key, allowed, n=1)
            hint = f" ¿Quisiste decir '{close[0]}'?" if close else ""
            warn(f"{where}: clave desconocida '{key}'.{hint}")


def _expect_dict(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise StoryError(f"{what} debe ser un objeto JSON, no {value!r}")
    return value


# --------------------------------------------------------------------------- #
# Estilos
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TextStyle:
    """Estilo tipográfico del texto.

    font:  ruta a un .ttf/.otf (relativa a la raíz del proyecto), nombre de una
           fuente del sistema ('arial', 'georgia'...) o None para la de Pygame.
    size:  tamaño en píxeles a la resolución de diseño (1280x720); se reescala
           automáticamente si la ventana cambia de tamaño.
    """

    color: Color = DEFAULT_TEXT_COLOR
    size: int = DEFAULT_TEXT_SIZE
    font: Optional[str] = None

    @classmethod
    def from_dict(
        cls,
        data: Optional[Dict[str, Any]],
        base: Optional["TextStyle"] = None,
        warn: Warn = _ignore,
        where: str = "text_style",
    ) -> "TextStyle":
        """Crea un estilo; las claves ausentes se heredan de `base`."""
        base = base or cls()
        data = _expect_dict(data, where) if data is not None else {}
        _check_keys(data, ("color", "size", "font"), where, warn)
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

    x, y:    punto de la pantalla donde se coloca el `anchor` de la imagen, en
             coordenadas de la resolución de diseño. Si falta alguno, la imagen
             se centra en ese eje.
    scale:   multiplicador de tamaño (1.0 = sin cambios).
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
    def from_dict(
        cls, data: Optional[Dict[str, Any]], warn: Warn = _ignore, where: str = "background"
    ) -> "BackgroundStyle":
        data = _expect_dict(data, where) if data is not None else {}
        _check_keys(data, ("image", "fit", "scale", "x", "y", "anchor", "fill"), where, warn)
        fit = data.get("fit", "cover")
        if fit not in VALID_FITS:
            raise StoryError(f"'fit' debe ser uno de {sorted(VALID_FITS)}, no {fit!r}")
        anchor = data.get("anchor", "center")
        if anchor not in VALID_ANCHORS:
            raise StoryError(f"'anchor' debe ser uno de {sorted(VALID_ANCHORS)}, no {anchor!r}")
        scale = _number(data, "scale")
        scale = 1.0 if scale is None else scale
        if scale <= 0:
            raise StoryError(f"'scale' debe ser mayor que 0, no {scale!r}")
        return cls(
            image=data.get("image"),
            fit=fit,
            scale=scale,
            x=_number(data, "x"),
            y=_number(data, "y"),
            anchor=anchor,
            fill=parse_color(data.get("fill"), DEFAULT_FILL),
        )


@dataclass(frozen=True)
class Sprite:
    """Imagen colocada sobre el fondo (personaje, objeto...).

    Se posiciona con `position` ('left' | 'center' | 'right') o con `x`/`y`
    explícitos. Por defecto se ancla por abajo-centro al borde inferior.
    El tamaño se da con `height` (px de diseño, conserva proporciones) o `scale`.
    """

    image: str
    position: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    anchor: str = "midbottom"
    scale: float = 1.0
    height: Optional[float] = None
    flip: bool = False
    requires: Tuple[Condition, ...] = ()

    @classmethod
    def from_dict(cls, data: Any, warn: Warn = _ignore, where: str = "characters") -> "Sprite":
        data = _expect_dict(data, where)
        _check_keys(
            data,
            ("image", "position", "x", "y", "anchor", "scale", "height", "flip", "requires"),
            where, warn,
        )
        image = data.get("image")
        if not isinstance(image, str) or not image:
            raise StoryError(f"{where}: falta 'image'")
        position = data.get("position")
        if position is not None and position not in VALID_POSITIONS:
            raise StoryError(f"{where}: 'position' debe ser uno de {sorted(VALID_POSITIONS)}")
        anchor = data.get("anchor", "midbottom")
        if anchor not in VALID_ANCHORS:
            raise StoryError(f"{where}: 'anchor' debe ser uno de {sorted(VALID_ANCHORS)}")
        scale = _number(data, "scale")
        scale = 1.0 if scale is None else scale
        height = _number(data, "height")
        if scale <= 0 or (height is not None and height <= 0):
            raise StoryError(f"{where}: 'scale' y 'height' deben ser mayores que 0")
        return cls(
            image=image, position=position, x=_number(data, "x"), y=_number(data, "y"),
            anchor=anchor, scale=scale, height=height, flip=bool(data.get("flip", False)),
            requires=parse_requires(data.get("requires")),
        )


# --------------------------------------------------------------------------- #
# Nodos del árbol
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DialogueLine:
    """Un paso de texto dentro de una escena.

    `style` ya viene resuelto (estilo de la escena + sobrescrituras de la línea).
    `requires` permite que la línea sólo aparezca si se cumple una condición.
    """

    text: str
    style: TextStyle
    speaker: Optional[str] = None
    speaker_color: Optional[Color] = None
    sfx: Optional[str] = None
    set: Tuple[Effect, ...] = ()
    requires: Tuple[Condition, ...] = ()

    @classmethod
    def from_data(
        cls, data: Any, scene_style: TextStyle, warn: Warn = _ignore, where: str = "dialogue"
    ) -> "DialogueLine":
        if isinstance(data, str):  # atajo: una línea puede ser sólo un texto
            return cls(text=data, style=scene_style)
        data = _expect_dict(data, where)
        _check_keys(
            data,
            ("text", "speaker", "speaker_color", "text_style", "sfx", "set", "requires"),
            where, warn,
        )
        if "text" not in data:
            raise StoryError(f"{where}: falta 'text'")
        speaker = data.get("speaker")
        return cls(
            text=str(data["text"]),
            style=TextStyle.from_dict(data.get("text_style"), scene_style, warn, f"{where}.text_style"),
            speaker=None if speaker is None else str(speaker),
            speaker_color=(
                parse_color(data["speaker_color"], scene_style.color)
                if "speaker_color" in data else None
            ),
            sfx=data.get("sfx"),
            set=parse_effects(data.get("set")),
            requires=parse_requires(data.get("requires")),
        )


@dataclass(frozen=True)
class Choice:
    """Una rama: texto del botón + ID de la escena a la que lleva.

    requires:     condiciones para poder elegirla.
    if_locked:    'hide' (se oculta) o 'disable' (se muestra deshabilitada).
    locked_hint:  texto extra que se añade a la opción deshabilitada.
    set:          efectos que se aplican al elegirla.
    """

    text: str
    target: str
    requires: Tuple[Condition, ...] = ()
    set: Tuple[Effect, ...] = ()
    if_locked: str = "hide"
    locked_hint: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Any, warn: Warn = _ignore, where: str = "choices") -> "Choice":
        data = _expect_dict(data, where)
        _check_keys(
            data, ("text", "target", "requires", "set", "if_locked", "locked_hint"), where, warn
        )
        for key in ("text", "target"):
            if key not in data:
                raise StoryError(f"{where}: falta '{key}'")
        if_locked = data.get("if_locked", "hide")
        if if_locked not in VALID_IF_LOCKED:
            raise StoryError(f"{where}: 'if_locked' debe ser uno de {sorted(VALID_IF_LOCKED)}")
        hint = data.get("locked_hint")
        return cls(
            text=str(data["text"]),
            target=str(data["target"]),
            requires=parse_requires(data.get("requires")),
            set=parse_effects(data.get("set")),
            if_locked=if_locked,
            locked_hint=None if hint is None else str(hint),
        )


@dataclass(frozen=True)
class Scene:
    """Un nodo del árbol de escenas."""

    id: str
    lines: Tuple[DialogueLine, ...]
    text_style: TextStyle
    background: BackgroundStyle
    sprites: Tuple[Sprite, ...]
    music: Optional[str]
    music_volume: float
    sfx: Optional[str]
    transition_ms: Optional[int]  # None = usar el valor por defecto de la historia
    on_enter: Tuple[Effect, ...]
    choices: Tuple[Choice, ...]

    @property
    def text(self) -> str:
        """Todo el texto de la escena (útil para depurar y para tests)."""
        return "\n".join(line.text for line in self.lines)

    @property
    def is_ending(self) -> bool:
        """Una escena sin opciones es un final."""
        return not self.choices

    @classmethod
    def from_dict(cls, data: Any, default_style: TextStyle, warn: Warn = _ignore) -> "Scene":
        data = _expect_dict(data, "Cada escena")
        scene_id = data.get("id")
        if not isinstance(scene_id, str) or not scene_id.strip():
            raise StoryError(f"Cada escena necesita un 'id' de texto no vacío: {data!r}")
        where = f"Escena '{scene_id}'"
        try:
            _check_keys(
                data,
                ("id", "text", "dialogue", "text_style", "background", "characters", "music",
                 "music_volume", "sfx", "transition", "on_enter", "choices"),
                where, warn,
            )
            style = TextStyle.from_dict(data.get("text_style"), default_style, warn, f"{where}.text_style")
            return cls(
                id=scene_id,
                lines=cls._parse_lines(data, style, warn, where),
                text_style=style,
                background=BackgroundStyle.from_dict(
                    data.get("background"), warn, f"{where}.background"
                ),
                sprites=tuple(
                    Sprite.from_dict(s, warn, f"{where}.characters[{i}]")
                    for i, s in enumerate(data.get("characters", []))
                ),
                music=data.get("music"),
                music_volume=cls._parse_volume(data.get("music_volume")),
                sfx=data.get("sfx"),
                transition_ms=cls._parse_transition(data.get("transition")),
                on_enter=parse_effects(data.get("on_enter")),
                choices=tuple(
                    Choice.from_dict(c, warn, f"{where}.choices[{i}]")
                    for i, c in enumerate(data.get("choices", []))
                ),
            )
        except StoryError as exc:
            if str(exc).startswith(where):
                raise
            raise StoryError(f"{where}: {exc}") from exc

    @staticmethod
    def _parse_lines(
        data: Dict[str, Any], style: TextStyle, warn: Warn, where: str
    ) -> Tuple[DialogueLine, ...]:
        if "dialogue" in data:
            if "text" in data:
                warn(f"{where}: tiene 'text' y 'dialogue'; se usa sólo 'dialogue'")
            raw = data["dialogue"]
            if not isinstance(raw, list) or not raw:
                raise StoryError("'dialogue' debe ser una lista con al menos una línea")
            return tuple(
                DialogueLine.from_data(item, style, warn, f"{where}.dialogue[{i}]")
                for i, item in enumerate(raw)
            )
        if "text" in data:
            return (DialogueLine(text=str(data["text"]), style=style),)
        raise StoryError("necesita 'text' o 'dialogue'")

    @staticmethod
    def _parse_volume(value: Any) -> float:
        if value is None:
            return 1.0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise StoryError(f"'music_volume' debe estar entre 0 y 1, no {value!r}")
        return float(value)

    @staticmethod
    def _parse_transition(value: Any) -> Optional[int]:
        """'cut' | 'fade' | {"type": "fade"|"cut", "ms": 600}  ->  milisegundos."""
        if value is None:
            return None
        if isinstance(value, str):
            value = {"type": value}
        value = _expect_dict(value, "'transition'")
        kind = value.get("type", "fade")
        if kind not in ("fade", "cut"):
            raise StoryError(f"'transition.type' debe ser 'fade' o 'cut', no {kind!r}")
        if kind == "cut":
            return 0
        ms = value.get("ms")
        if ms is None:
            return None
        if isinstance(ms, bool) or not isinstance(ms, int) or ms < 0:
            raise StoryError(f"'transition.ms' debe ser un entero >= 0, no {ms!r}")
        return ms


@dataclass(frozen=True)
class StorySettings:
    """Ajustes globales de la historia (clave `settings` del JSON)."""

    title: str = "Novela Visual"
    width: int = 1280
    height: int = 720
    default_text_style: TextStyle = TextStyle()
    restart_text: str = "Volver a empezar"
    music_fade_ms: int = 800
    default_transition_ms: int = 500
    title_background: Optional[BackgroundStyle] = None
    title_music: Optional[str] = None
    variables: Dict[str, Any] = field(default_factory=dict)   # valores iniciales de una partida nueva

    @classmethod
    def from_dict(cls, data: Any, warn: Warn = _ignore) -> "StorySettings":
        data = _expect_dict(data, "'settings'") if data is not None else {}
        _check_keys(
            data,
            ("window", "default_text_style", "restart_text", "music_fade_ms",
             "default_transition_ms", "title_background", "title_music", "variables"),
            "settings", warn,
        )
        window = _expect_dict(data.get("window", {}), "'settings.window'")
        _check_keys(window, ("width", "height", "title"), "settings.window", warn)
        default = cls()
        for key in ("width", "height"):
            value = window.get(key, getattr(default, key))
            if isinstance(value, bool) or not isinstance(value, int) or value < 200:
                raise StoryError(f"'settings.window.{key}' debe ser un entero >= 200, no {value!r}")
        for key in ("music_fade_ms", "default_transition_ms"):
            value = data.get(key, getattr(default, key))
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise StoryError(f"'settings.{key}' debe ser un entero >= 0, no {value!r}")
        variables = _expect_dict(data.get("variables", {}), "'settings.variables'")
        for name, value in variables.items():
            if not isinstance(value, (bool, int, float, str)):
                raise StoryError(f"'settings.variables.{name}' debe ser un valor simple, no {value!r}")
        bg = data.get("title_background")
        return cls(
            title=str(window.get("title", default.title)),
            width=window.get("width", default.width),
            height=window.get("height", default.height),
            default_text_style=TextStyle.from_dict(
                data.get("default_text_style"), None, warn, "settings.default_text_style"
            ),
            restart_text=str(data.get("restart_text", default.restart_text)),
            music_fade_ms=data.get("music_fade_ms", default.music_fade_ms),
            default_transition_ms=data.get("default_transition_ms", default.default_transition_ms),
            title_background=(
                BackgroundStyle.from_dict(bg, warn, "settings.title_background") if bg else None
            ),
            title_music=data.get("title_music"),
            variables=dict(variables),
        )


class Story:
    """Árbol completo de escenas, indexado por ID."""

    def __init__(
        self,
        scenes: Iterable[Scene],
        start_id: str,
        settings: StorySettings,
        warnings: Optional[List[str]] = None,
    ) -> None:
        self.scenes: Dict[str, Scene] = {}
        for scene in scenes:
            if scene.id in self.scenes:
                raise StoryError(f"ID de escena duplicado: '{scene.id}'")
            self.scenes[scene.id] = scene
        self.start_id = start_id
        self.settings = settings
        self.warnings: List[str] = list(warnings or [])
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
    def from_dict(cls, data: Any) -> "Story":
        data = _expect_dict(data, "La raíz del JSON")
        warnings: List[str] = []
        warn = warnings.append
        _check_keys(data, ("start", "settings", "scenes"), "raíz", warn)
        raw_scenes = data.get("scenes")
        if not isinstance(raw_scenes, list) or not raw_scenes:
            raise StoryError("El JSON debe incluir una lista 'scenes' con al menos una escena")
        try:
            settings = StorySettings.from_dict(data.get("settings"), warn)
        except StoryError as exc:
            raise StoryError(f"settings: {exc}") from exc
        scenes = [Scene.from_dict(raw, settings.default_text_style, warn) for raw in raw_scenes]
        start_id = data.get("start") or scenes[0].id
        return cls(scenes, start_id, settings, warnings)

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
        """Comprueba destinos y avisa de escenas inalcanzables."""
        if self.start_id not in self.scenes:
            raise StoryError(f"La escena inicial '{self.start_id}' no existe")
        for scene in self.scenes.values():
            for choice in scene.choices:
                if choice.target not in self.scenes:
                    raise StoryError(
                        f"Escena '{scene.id}': la opción '{choice.text}' "
                        f"apunta a una escena inexistente ('{choice.target}')"
                    )
        for scene_id in sorted(set(self.scenes) - self.reachable_ids()):
            self.warnings.append(f"Escena '{scene_id}': no se puede llegar a ella desde el inicio")

    def reachable_ids(self) -> Set[str]:
        """Escenas alcanzables desde el inicio (ignorando condiciones)."""
        seen = {self.start_id}
        queue = deque([self.start_id])
        while queue:
            for choice in self.scenes[queue.popleft()].choices:
                if choice.target not in seen:
                    seen.add(choice.target)
                    queue.append(choice.target)
        return seen

    # -- Recursos ---------------------------------------------------------- #
    def referenced_assets(self) -> Iterator[Tuple[str, str]]:
        """Itera (ruta, dónde se usa) de todos los archivos que cita la historia."""
        settings = self.settings
        if settings.title_background and settings.title_background.image:
            yield settings.title_background.image, "settings.title_background"
        if settings.title_music:
            yield settings.title_music, "settings.title_music"
        if _is_font_file(settings.default_text_style.font):
            yield settings.default_text_style.font, "settings.default_text_style"  # type: ignore[misc]
        for scene in self.scenes.values():
            where = f"escena '{scene.id}'"
            if scene.background.image:
                yield scene.background.image, where
            for sprite in scene.sprites:
                yield sprite.image, where
            if scene.music:
                yield scene.music, where
            if scene.sfx:
                yield scene.sfx, where
            if _is_font_file(scene.text_style.font):
                yield scene.text_style.font, where  # type: ignore[misc]
            for line in scene.lines:
                if line.sfx:
                    yield line.sfx, where
                if _is_font_file(line.style.font):
                    yield line.style.font, where  # type: ignore[misc]

    def missing_assets(self, base_dir: Path) -> List[str]:
        """Archivos citados que no existen en disco (una línea por archivo)."""
        missing: Dict[str, str] = {}
        for rel, where in self.referenced_assets():
            if rel not in missing and not (base_dir / rel).is_file():
                missing[rel] = where
        return [f"{rel}  ({where})" for rel, where in missing.items()]
