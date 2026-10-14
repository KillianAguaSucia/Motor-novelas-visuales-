"""ui.py — Renderizado: fondo, sprites, diálogo, botones y menús.

Diseño "responsivo":
  * Las coordenadas y tamaños del JSON están pensados para una resolución de
    DISEÑO de 1280x720. Si la ventana cambia de tamaño, todo se reescala.
  * `layout`/`prepare` calculan posiciones y superficies UNA vez (por escena y
    tamaño de ventana); `draw` sólo pinta lo ya calculado.

Piezas principales:
    ResourceCache   imágenes y fuentes con caché (las fuentes, con tope LRU)
    DialogueBox     caja de texto: word wrap, páginas, máquina de escribir, hablante
    UI              una escena de juego: fondo + sprites (con fundido) + diálogo + botones
    MenuView        pantallas de menú (título, pausa, ajustes, ranuras)
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pygame

from scene import BackgroundStyle, DialogueLine, Scene, Sprite, TextStyle

log = logging.getLogger(__name__)

BASE_SIZE = (1280, 720)      # resolución de diseño del JSON
MAX_SURFACE = 8192           # límite de seguridad para imágenes escaladas
MIN_FONT_PX = 12             # tamaño mínimo de fuente
FONT_CACHE_LIMIT = 96        # fuentes distintas en memoria (al redimensionar salen muchas)
PRESET_X = {"left": 0.2, "center": 0.5, "right": 0.8}   # fracción del ancho de diseño

# Tema visual (RGB / RGBA)
PANEL_COLOR = (12, 12, 24, 205)
PANEL_BORDER = (255, 255, 255, 70)
BUTTON_COLOR = (38, 52, 92)
BUTTON_FOCUS_COLOR = (70, 96, 168)
BUTTON_BORDER = (200, 210, 240)
BUTTON_TEXT = (255, 255, 255)
DISABLED_COLOR = (40, 42, 52)
DISABLED_BORDER = (90, 92, 104)
DISABLED_TEXT = (135, 138, 150)
DIM_COLOR = (0, 0, 0, 195)

Size = Tuple[int, int]


def ui_scale(size: Size) -> float:
    """Factor de escala de la interfaz respecto a la resolución de diseño."""
    return min(size[0] / BASE_SIZE[0], size[1] / BASE_SIZE[1])


# --------------------------------------------------------------------------- #
# Recursos (imágenes y fuentes) con caché
# --------------------------------------------------------------------------- #
class ResourceCache:
    """Carga imágenes y fuentes una sola vez; los fallos avisan y no rompen el juego."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._images: Dict[str, Optional[pygame.Surface]] = {}
        self._fonts: "OrderedDict[Tuple[Optional[str], int], pygame.font.Font]" = OrderedDict()
        self._warned: Set[str] = set()

    def image(self, rel_path: str) -> Optional[pygame.Surface]:
        if rel_path not in self._images:
            try:
                surface = pygame.image.load(str(self._base_dir / rel_path)).convert_alpha()
            except (pygame.error, FileNotFoundError) as exc:
                self._warn_once(rel_path, f"No se pudo cargar la imagen '{rel_path}': {exc}")
                surface = None
            self._images[rel_path] = surface
        return self._images[rel_path]

    def font(self, name: Optional[str], size_px: int) -> pygame.font.Font:
        """`name`: ruta .ttf/.otf, nombre de fuente del sistema o None (por defecto)."""
        key = (name, size_px)
        font = self._fonts.get(key)
        if font is None:
            font = self._load_font(name, size_px)
            self._fonts[key] = font
            if len(self._fonts) > FONT_CACHE_LIMIT:
                self._fonts.popitem(last=False)   # expulsa la menos usada
        else:
            self._fonts.move_to_end(key)
        return font

    def _load_font(self, name: Optional[str], size_px: int) -> pygame.font.Font:
        if not name:
            return pygame.font.Font(None, size_px)
        if Path(name).suffix.lower() in (".ttf", ".otf"):
            try:
                return pygame.font.Font(str(self._base_dir / name), size_px)
            except (pygame.error, FileNotFoundError, OSError) as exc:
                self._warn_once(name, f"No se pudo cargar la fuente '{name}': {exc}. Uso la de Pygame.")
                return pygame.font.Font(None, size_px)
        return pygame.font.SysFont(name, size_px)  # si no existe, Pygame usa una por defecto

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)


# --------------------------------------------------------------------------- #
# Funciones de layout puras
# --------------------------------------------------------------------------- #
def wrap_text(text: str, font: pygame.font.Font, max_width: int) -> List[str]:
    """Divide `text` en líneas que quepan en `max_width` píxeles (word wrap).

    Respeta los saltos de línea explícitos ('\\n') y parte las palabras
    individuales que, por sí solas, sean más anchas que la línea.
    """
    lines: List[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")  # línea en blanco
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if font.size(candidate)[0] <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            # Palabra más ancha que la línea: partirla por caracteres.
            while font.size(word)[0] > max_width and len(word) > 1:
                cut = len(word) - 1
                while cut > 1 and font.size(word[:cut])[0] > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines


def paginate(lines: List[str], per_page: int) -> List[List[str]]:
    """Reparte líneas en páginas de `per_page` líneas (sin blancos al borde de página)."""
    pages: List[List[str]] = []
    for start in range(0, len(lines), per_page):
        chunk = lines[start:start + per_page]
        while len(chunk) > 1 and pages and not chunk[0].strip():
            chunk = chunk[1:]
        while len(chunk) > 1 and not chunk[-1].strip():
            chunk = chunk[:-1]
        pages.append(chunk)
    return pages or [[""]]


def place_background(bg: BackgroundStyle, image_size: Size, window_size: Size) -> pygame.Rect:
    """Calcula el rectángulo (tamaño y posición) de la imagen de fondo."""
    win_w, win_h = window_size
    img_w, img_h = image_size
    ratio_x, ratio_y = win_w / BASE_SIZE[0], win_h / BASE_SIZE[1]

    if bg.fit == "cover":
        base = max(win_w / img_w, win_h / img_h)
    elif bg.fit == "contain":
        base = min(win_w / img_w, win_h / img_h)
    else:  # "none": tamaño nativo, proporcional al tamaño de la ventana
        base = min(ratio_x, ratio_y)
    factor = base * bg.scale

    width = max(1, min(MAX_SURFACE, round(img_w * factor)))
    height = max(1, min(MAX_SURFACE, round(img_h * factor)))

    # Sin x/y -> centrada en ese eje; con x/y -> en coordenadas de diseño.
    pos_x = win_w / 2 if bg.x is None else bg.x * ratio_x
    pos_y = win_h / 2 if bg.y is None else bg.y * ratio_y

    rect = pygame.Rect(0, 0, width, height)
    setattr(rect, bg.anchor, (round(pos_x), round(pos_y)))
    return rect


def place_sprite(sprite: Sprite, image_size: Size, window_size: Size) -> pygame.Rect:
    """Calcula el rectángulo de un sprite (tamaño por `height` o `scale`)."""
    win_w, win_h = window_size
    img_w, img_h = image_size
    ratio_x, ratio_y = win_w / BASE_SIZE[0], win_h / BASE_SIZE[1]
    if sprite.height is not None:
        factor = sprite.height * ui_scale(window_size) / img_h
    else:
        factor = sprite.scale * ui_scale(window_size)
    width = max(1, min(MAX_SURFACE, round(img_w * factor)))
    height = max(1, min(MAX_SURFACE, round(img_h * factor)))

    preset_x = PRESET_X[sprite.position or "center"] * BASE_SIZE[0]
    design_x = preset_x if sprite.x is None else sprite.x
    design_y = BASE_SIZE[1] if sprite.y is None else sprite.y

    rect = pygame.Rect(0, 0, width, height)
    setattr(rect, sprite.anchor, (round(design_x * ratio_x), round(design_y * ratio_y)))
    return rect


def render_background_layer(res: ResourceCache, bg: BackgroundStyle, size: Size) -> pygame.Surface:
    """Superficie del tamaño de la ventana con el color de relleno y la imagen colocada."""
    layer = pygame.Surface(size).convert()
    layer.fill(bg.fill)
    image = res.image(bg.image) if bg.image else None
    if image is not None:
        rect = place_background(bg, image.get_size(), size)
        layer.blit(pygame.transform.smoothscale(image, rect.size), rect)
    return layer


def render_sprite(
    res: ResourceCache, sprite: Sprite, size: Size
) -> Optional[Tuple[pygame.Surface, pygame.Rect]]:
    image = res.image(sprite.image)
    if image is None:
        return None
    rect = place_sprite(sprite, image.get_size(), size)
    surface = pygame.transform.smoothscale(image, rect.size)
    if sprite.flip:
        surface = pygame.transform.flip(surface, True, False)
    return surface, rect


# --------------------------------------------------------------------------- #
# Botones
# --------------------------------------------------------------------------- #
def draw_button_box(screen: pygame.Surface, rect: pygame.Rect, enabled: bool, focused: bool) -> None:
    """Fondo y borde de un botón; el foco (ratón o teclado) lo resalta."""
    radius = max(4, rect.height // 4)
    if not enabled:
        fill, border, width = DISABLED_COLOR, DISABLED_BORDER, 1
    elif focused:
        fill, border, width = BUTTON_FOCUS_COLOR, (255, 255, 255), 3
    else:
        fill, border, width = BUTTON_COLOR, BUTTON_BORDER, 2
    pygame.draw.rect(screen, fill, rect, border_radius=radius)
    pygame.draw.rect(screen, border, rect, width=width, border_radius=radius)


@dataclass
class Button:
    """Botón ya maquetado (puede tener varias líneas de texto)."""

    rect: pygame.Rect
    labels: List[pygame.Surface]
    line_height: int
    enabled: bool = True

    def draw(self, screen: pygame.Surface, focused: bool) -> None:
        draw_button_box(screen, self.rect, self.enabled, focused)
        y = self.rect.centery - self.line_height * len(self.labels) // 2
        for label in self.labels:
            screen.blit(label, label.get_rect(midtop=(self.rect.centerx, y)))
            y += self.line_height


class ButtonList:
    """Lista de botones con un elemento enfocado (teclado, mando o ratón)."""

    def __init__(self) -> None:
        self.buttons: List[Button] = []
        self.focus = 0

    def set(self, buttons: List[Button]) -> None:
        self.buttons = buttons
        self.focus = next((i for i, b in enumerate(buttons) if b.enabled), 0)

    def move(self, delta: int) -> None:
        """Mueve el foco saltando los botones deshabilitados."""
        count = len(self.buttons)
        for step in range(1, count + 1):
            candidate = (self.focus + delta * step) % count
            if self.buttons[candidate].enabled:
                self.focus = candidate
                return

    def index_at(self, pos: Tuple[int, int]) -> Optional[int]:
        for i, button in enumerate(self.buttons):
            if button.rect.collidepoint(pos):
                return i
        return None

    def hover(self, pos: Tuple[int, int]) -> None:
        index = self.index_at(pos)
        if index is not None and self.buttons[index].enabled:
            self.focus = index

    def draw(self, screen: pygame.Surface) -> None:
        for i, button in enumerate(self.buttons):
            button.draw(screen, focused=(i == self.focus))


@dataclass(frozen=True)
class ChoiceItem:
    """Opción a mostrar como botón (el texto ya incluye pistas como '(bloqueado)')."""

    text: str
    enabled: bool = True


def layout_choice_buttons(
    res: ResourceCache,
    style: TextStyle,
    items: Sequence[ChoiceItem],
    size: Size,
    bottom_y: int,
) -> List[Button]:
    """Coloca los botones de opciones hacia arriba desde `bottom_y`.

    Si no caben, primero reduce la fuente; si aun así no caben, usa dos columnas.
    """
    scale = ui_scale(size)
    win_w = size[0]
    pad_x, pad_y, gap = round(22 * scale), round(10 * scale), round(10 * scale)
    top_limit = round(8 * scale)
    count = len(items)

    def build(columns: int, font_px: int) -> Tuple[List[List[str]], pygame.font.Font, int, List[int]]:
        font = res.font(style.font, font_px)
        max_w = round(win_w * (0.8 if columns == 1 else 0.46))
        wrapped = [
            wrap_text(f"{i + 1}. {item.text}", font, max_w - 2 * pad_x)
            for i, item in enumerate(items)
        ]
        line_h = font.get_linesize()
        heights = [line_h * len(lines) + 2 * pad_y for lines in wrapped]
        return wrapped, font, line_h, heights

    def column_split(columns: int) -> List[range]:
        if columns == 1:
            return [range(count)]
        half = (count + 1) // 2
        return [range(half), range(half, count)]

    def total_height(columns: int, heights: List[int]) -> int:
        return max(
            sum(heights[i] for i in col) + gap * (len(col) - 1) for col in column_split(columns)
        )

    base_px = max(MIN_FONT_PX, round(style.size * scale * 0.9))
    chosen = None
    for columns in (1, 2):
        font_px = base_px
        while True:
            wrapped, font, line_h, heights = build(columns, font_px)
            fits = bottom_y - total_height(columns, heights) >= top_limit
            if fits or font_px <= MIN_FONT_PX:
                break
            font_px -= 2
        chosen = (columns, wrapped, font, line_h, heights)
        if fits or count < 2:
            break
    assert chosen is not None
    columns, wrapped, font, line_h, heights = chosen

    max_w = round(win_w * (0.8 if columns == 1 else 0.46))
    text_w = max(font.size(line)[0] for lines in wrapped for line in lines)
    btn_w = min(max_w, text_w + 2 * pad_x)
    block_w = btn_w * columns + gap * (columns - 1)
    left = (win_w - block_w) // 2

    buttons: List[Optional[Button]] = [None] * count
    for col_index, col in enumerate(column_split(columns)):
        y = bottom_y
        for i in reversed(col):
            rect = pygame.Rect(left + col_index * (btn_w + gap), 0, btn_w, heights[i])
            rect.bottom = y
            y = rect.top - gap
            enabled = items[i].enabled
            color = BUTTON_TEXT if enabled else DISABLED_TEXT
            labels = [font.render(line, True, color) for line in wrapped[i] if line]
            buttons[i] = Button(rect, labels, line_h, enabled)
    return [b for b in buttons if b is not None]


# --------------------------------------------------------------------------- #
# Caja de diálogo
# --------------------------------------------------------------------------- #
class DialogueBox:
    """Panel de texto inferior: word wrap, páginas y efecto máquina de escribir.

    Si el texto no cabe en el panel NO se encoge: se reparte en páginas y el
    jugador avanza con "continuar". El panel mide lo mismo en todas las páginas.
    """

    MAX_HEIGHT_RATIO = 0.42   # el panel nunca pasa de este % del alto de la ventana
    MIN_HEIGHT_RATIO = 0.24

    def __init__(self, res: ResourceCache) -> None:
        self._res = res
        self._text = ""
        self._style = TextStyle()
        self._speaker: Optional[str] = None
        self._speaker_color: Optional[Tuple[int, int, int]] = None

        self.panel_rect = pygame.Rect(0, 0, 0, 0)
        self.reserved_top = 0   # alto que la etiqueta del hablante ocupa sobre el panel
        self._scale = 1.0
        self._panel_surface: Optional[pygame.Surface] = None
        self._tab: Optional[Tuple[pygame.Surface, pygame.Rect, pygame.Surface]] = None
        self._pages: List[List[str]] = [[""]]
        self._page = 0
        self._reveal = 0.0
        self._font: Optional[pygame.font.Font] = None
        self._line_h = 0
        self._pad = 0
        self._line_cache: Dict[Tuple[int, int], pygame.Surface] = {}

    # -- Contenido --------------------------------------------------------- #
    def set_text(
        self,
        text: str,
        style: TextStyle,
        speaker: Optional[str] = None,
        speaker_color: Optional[Tuple[int, int, int]] = None,
    ) -> None:
        """Cambia el texto y reinicia la máquina de escribir (llamar a `layout` después)."""
        self._text, self._style = text, style
        self._speaker, self._speaker_color = speaker, speaker_color
        self._page, self._reveal = 0, 0.0

    def layout(self, size: Size) -> None:
        scale = self._scale = ui_scale(size)
        win_w, win_h = size
        style = self._style
        margin, pad = round(16 * scale), round(24 * scale)
        self._pad = pad
        panel_w = win_w - 2 * margin

        font = self._font = self._res.font(style.font, max(MIN_FONT_PX, round(style.size * scale)))
        self._line_h = font.get_linesize()
        wrapped = wrap_text(self._text, font, panel_w - 2 * pad)

        max_h = round(win_h * self.MAX_HEIGHT_RATIO)
        per_page = max(1, (max_h - 2 * pad) // self._line_h)
        self._pages = paginate(wrapped, per_page)
        self._page = min(self._page, len(self._pages) - 1)
        rows = max(len(page) for page in self._pages)
        panel_h = min(
            max(round(win_h * self.MIN_HEIGHT_RATIO), rows * self._line_h + 2 * pad),
            win_h - 2 * margin,
        )
        self.panel_rect = pygame.Rect(margin, win_h - margin - panel_h, panel_w, panel_h)

        panel = pygame.Surface(self.panel_rect.size, pygame.SRCALPHA)
        radius = max(6, round(14 * scale))
        pygame.draw.rect(panel, PANEL_COLOR, panel.get_rect(), border_radius=radius)
        pygame.draw.rect(panel, PANEL_BORDER, panel.get_rect(), width=2, border_radius=radius)
        self._panel_surface = panel
        self._line_cache.clear()
        self._layout_speaker_tab(scale, pad, radius)

    def _layout_speaker_tab(self, scale: float, pad: int, radius: int) -> None:
        self._tab = None
        self.reserved_top = 0
        if not self._speaker:
            return
        style = self._style
        font = self._res.font(style.font, max(MIN_FONT_PX, round(style.size * scale * 0.85)))
        label = font.render(self._speaker, True, self._speaker_color or style.color)
        pad_x, pad_y = round(18 * scale), round(6 * scale)
        rect = pygame.Rect(0, 0, label.get_width() + 2 * pad_x, label.get_height() + 2 * pad_y)
        rect.bottomleft = (self.panel_rect.left + pad, self.panel_rect.top + round(2 * scale))
        tab = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(
            tab, PANEL_COLOR, tab.get_rect(),
            border_top_left_radius=radius, border_top_right_radius=radius,
        )
        pygame.draw.rect(
            tab, PANEL_BORDER, tab.get_rect(), width=2,
            border_top_left_radius=radius, border_top_right_radius=radius,
        )
        self._tab = (tab, rect, label)
        self.reserved_top = rect.height - round(2 * scale)

    # -- Progreso ---------------------------------------------------------- #
    @property
    def _page_chars(self) -> int:
        return sum(len(line) for line in self._pages[self._page])

    @property
    def page_complete(self) -> bool:
        """La página actual ya se ha escrito entera."""
        return self._reveal >= self._page_chars

    @property
    def has_next_page(self) -> bool:
        return self._page + 1 < len(self._pages)

    @property
    def complete(self) -> bool:
        """Se ha escrito todo el texto (última página incluida)."""
        return self.page_complete and not self.has_next_page

    def update(self, dt_ms: float, cps: float) -> None:
        """Avanza la máquina de escribir `cps` caracteres por segundo (0 = instantáneo)."""
        total = self._page_chars
        if self._reveal >= total:
            return
        self._reveal = total if cps <= 0 else min(total, self._reveal + cps * dt_ms / 1000)

    def skip(self) -> None:
        self._reveal = self._page_chars

    def next_page(self) -> None:
        if self.has_next_page:
            self._page += 1
            self._reveal = 0.0

    # -- Dibujo ------------------------------------------------------------ #
    def draw(self, screen: pygame.Surface, show_continue: bool) -> None:
        if self._panel_surface is None or self._font is None:
            return
        screen.blit(self._panel_surface, self.panel_rect)
        if self._tab is not None:
            tab, rect, label = self._tab
            screen.blit(tab, rect)
            screen.blit(label, label.get_rect(center=(rect.centerx, rect.centery + 1)))
        self._draw_text(screen)
        if show_continue and (pygame.time.get_ticks() // 450) % 2 == 0:
            self._draw_continue_marker(screen)

    def _draw_text(self, screen: pygame.Surface) -> None:
        assert self._font is not None
        remaining = int(self._reveal)
        y = self.panel_rect.top + self._pad
        for i, line in enumerate(self._pages[self._page]):
            if remaining <= 0:
                break
            if remaining >= len(line):
                key = (self._page, i)
                surface = self._line_cache.get(key)
                if surface is None and line.strip():
                    surface = self._font.render(line, True, self._style.color)
                    self._line_cache[key] = surface
                remaining -= len(line)
            else:  # línea a medio escribir
                surface = self._font.render(line[:remaining], True, self._style.color)
                remaining = 0
            if surface is not None:
                screen.blit(surface, (self.panel_rect.left + self._pad, y))
            y += self._line_h

    def _draw_continue_marker(self, screen: pygame.Surface) -> None:
        size = max(5, round(9 * self._scale))
        x = self.panel_rect.right - self._pad
        y = self.panel_rect.bottom - round(self._pad * 0.7)
        pygame.draw.polygon(
            screen, self._style.color, [(x - size, y - size), (x + size, y - size), (x, y)]
        )


# --------------------------------------------------------------------------- #
# Pantalla de juego
# --------------------------------------------------------------------------- #
class UI:
    """Una escena en pantalla: fondo + sprites (con fundido) + diálogo + opciones.

    Uso desde el motor, en este orden:
        set_scene(...)   al entrar en una escena (arranca el fundido)
        set_line(...)    al mostrar cada línea de diálogo
        set_choices(...) con las opciones (lista vacía mientras quedan líneas)
        relayout(size)   si cambia el tamaño de la ventana
    """

    def __init__(self, resources: ResourceCache) -> None:
        self._res = resources
        self._size: Size = BASE_SIZE
        self._scene: Optional[Scene] = None
        self._sprites: List[Sprite] = []
        self._layer: Optional[pygame.Surface] = None     # fondo + sprites ya compuestos
        self._prev: Optional[pygame.Surface] = None      # escena anterior (para el fundido)
        self._fade_ms = 0
        self._fade_elapsed = 0.0
        self._dialogue = DialogueBox(resources)
        self._line_style = TextStyle()
        self._choice_items: List[ChoiceItem] = []
        self._choices = ButtonList()

    # -- Preparación ------------------------------------------------------- #
    def reset(self) -> None:
        """Olvida la escena actual (p. ej. al volver al menú o cargar partida)."""
        self._scene = self._layer = self._prev = None
        self._sprites = []
        self._choice_items = []
        self._choices.set([])

    def set_scene(self, scene: Scene, sprites: Sequence[Sprite], size: Size, fade_ms: int) -> None:
        previous = self._layer if self._layer is not None and self._layer.get_size() == size else None
        self._scene, self._sprites, self._size = scene, list(sprites), size
        self._layer = self._build_layer()
        self._prev = previous if fade_ms > 0 else None
        self._fade_ms, self._fade_elapsed = fade_ms, 0.0

    def set_line(self, line: DialogueLine, text: str) -> None:
        """Muestra una línea (`text` ya viene con las variables sustituidas)."""
        self._line_style = line.style
        self._dialogue.set_text(text, line.style, line.speaker, line.speaker_color)
        self._dialogue.layout(self._size)
        self._layout_choices()

    def set_choices(self, items: Sequence[ChoiceItem]) -> None:
        self._choice_items = list(items)
        self._layout_choices()

    def relayout(self, size: Size) -> None:
        """Recalcula todo para un nuevo tamaño de ventana (sin fundido)."""
        self._size = size
        self._prev = None
        if self._scene is not None:
            self._layer = self._build_layer()
            self._dialogue.layout(size)
            self._layout_choices()

    def _build_layer(self) -> pygame.Surface:
        if self._scene is None:
            return pygame.Surface(self._size).convert()
        layer = render_background_layer(self._res, self._scene.background, self._size)
        for sprite in self._sprites:
            rendered = render_sprite(self._res, sprite, self._size)
            if rendered is not None:
                layer.blit(*rendered)
        return layer

    def _layout_choices(self) -> None:
        if not self._choice_items or self._scene is None:
            self._choices.set([])
            return
        gap = round(10 * ui_scale(self._size))
        bottom = self._dialogue.panel_rect.top - gap - self._dialogue.reserved_top
        self._choices.set(
            layout_choice_buttons(self._res, self._line_style, self._choice_items, self._size, bottom)
        )

    # -- Estado y entrada -------------------------------------------------- #
    @property
    def typing(self) -> bool:
        """Hay texto a medio escribir en la página actual."""
        return not self._dialogue.page_complete

    @property
    def has_next_page(self) -> bool:
        return self._dialogue.has_next_page

    @property
    def choices_visible(self) -> bool:
        """Las opciones se muestran cuando todo el texto de la última línea ya se escribió."""
        return bool(self._choice_items) and self._dialogue.complete

    @property
    def focus(self) -> int:
        return self._choices.focus

    def skip_typing(self) -> None:
        self._dialogue.skip()

    def next_page(self) -> None:
        self._dialogue.next_page()

    def move_focus(self, delta: int) -> None:
        if self.choices_visible:
            self._choices.move(delta)

    def hover(self, pos: Tuple[int, int]) -> None:
        if self.choices_visible:
            self._choices.hover(pos)

    def button_at(self, pos: Tuple[int, int]) -> Optional[int]:
        """Índice de la opción visible bajo `pos`, o None."""
        return self._choices.index_at(pos) if self.choices_visible else None

    def choice_rect(self, index: int) -> Optional[pygame.Rect]:
        """Rectángulo en pantalla de la opción `index` (None si no hay botones visibles)."""
        if not self.choices_visible or not 0 <= index < len(self._choices.buttons):
            return None
        return self._choices.buttons[index].rect.copy()

    def is_enabled(self, index: int) -> bool:
        return 0 <= index < len(self._choices.buttons) and self._choices.buttons[index].enabled

    # -- Fotograma --------------------------------------------------------- #
    def update(self, dt_ms: float, cps: float) -> None:
        if self._prev is not None:
            self._fade_elapsed += dt_ms
            if self._fade_elapsed >= self._fade_ms:
                self._prev = None
        self._dialogue.update(dt_ms, cps)

    def draw(self, screen: pygame.Surface) -> None:
        screen.fill((0, 0, 0))
        if self._layer is None:
            return
        screen.blit(self._layer, (0, 0))
        if self._prev is not None:
            self._prev.set_alpha(round(255 * (1 - self._fade_elapsed / self._fade_ms)))
            screen.blit(self._prev, (0, 0))
        self._dialogue.draw(screen, show_continue=self._dialogue.page_complete and not self.choices_visible)
        if self.choices_visible:
            self._choices.draw(screen)


# --------------------------------------------------------------------------- #
# Menús
# --------------------------------------------------------------------------- #
@dataclass
class MenuItem:
    """Elemento de menú: botón o deslizador (0..1)."""

    label: str
    kind: str = "button"   # "button" | "slider"
    enabled: bool = True
    value: float = 0.0
    value_text: str = ""


class MenuView:
    """Menú vertical centrado con título, botones y deslizadores."""

    def __init__(
        self,
        res: ResourceCache,
        style: TextStyle,
        title: str = "",
        background: Optional[BackgroundStyle] = None,
        dim: bool = False,
    ) -> None:
        self._res, self._style, self._title = res, style, title
        self._background, self._dim = background, dim
        self._items: List[MenuItem] = []
        self.focus = 0
        self._size: Size = BASE_SIZE
        self._bg_layer: Optional[pygame.Surface] = None
        self._dim_surface: Optional[pygame.Surface] = None
        self._title_blits: List[Tuple[pygame.Surface, Tuple[int, int]]] = []
        self._rects: List[pygame.Rect] = []
        self._tracks: Dict[int, pygame.Rect] = {}
        self._surfaces: List[Tuple[pygame.Surface, Optional[pygame.Surface]]] = []
        self._pad_x = 0
        self._pad_y = 0

    @property
    def items(self) -> List[MenuItem]:
        return self._items

    def set_items(self, items: Sequence[MenuItem]) -> None:
        self._items = list(items)
        if not (0 <= self.focus < len(self._items) and self._items[self.focus].enabled):
            self.focus = next((i for i, it in enumerate(self._items) if it.enabled), 0)

    def layout(self, size: Size) -> None:
        self._size = size
        scale = ui_scale(size)
        win_w, win_h = size
        res, style = self._res, self._style
        self._bg_layer = render_background_layer(res, self._background, size) if self._background else None
        self._dim_surface = None
        if self._dim:
            self._dim_surface = pygame.Surface(size, pygame.SRCALPHA)
            self._dim_surface.fill(DIM_COLOR)

        font_px = max(MIN_FONT_PX, round(style.size * 0.95 * scale))
        title_px = max(MIN_FONT_PX, round(style.size * 1.8 * scale))
        pad_y, gap, track_h = round(11 * scale), round(12 * scale), round(10 * scale)
        while True:   # si no cabe, se reducen las fuentes
            font = res.font(style.font, font_px)
            title_font = res.font(style.font, title_px)
            line_h = font.get_linesize()
            heights = [
                line_h + 2 * pad_y + (track_h + round(8 * scale) if it.kind == "slider" else 0)
                for it in self._items
            ]
            title_lines = wrap_text(self._title, title_font, round(win_w * 0.9)) if self._title else []
            title_h = len(title_lines) * title_font.get_linesize()
            title_gap = round(28 * scale) if title_lines else 0
            total = title_h + title_gap + sum(heights) + gap * max(0, len(heights) - 1)
            if total <= win_h * 0.94 or (font_px <= MIN_FONT_PX and title_px <= MIN_FONT_PX):
                break
            font_px, title_px = max(MIN_FONT_PX, font_px - 2), max(MIN_FONT_PX, title_px - 3)

        self._pad_x, self._pad_y = round(28 * scale), pad_y
        widest = max(
            (font.size(it.label)[0] + (font.size(it.value_text)[0] + round(30 * scale)
                                       if it.kind == "slider" else 0) for it in self._items),
            default=0,
        )
        width = min(round(win_w * 0.9), max(round(380 * scale), widest + 2 * self._pad_x))

        y = max(0, (win_h - total) // 2)
        self._title_blits = []
        for line in title_lines:
            shadow = title_font.render(line, True, (0, 0, 0))
            text = title_font.render(line, True, style.color)
            x = (win_w - text.get_width()) // 2
            self._title_blits += [(shadow, (x + 3, y + 3)), (text, (x, y))]
            y += title_font.get_linesize()
        y += title_gap

        self._rects, self._tracks, self._surfaces = [], {}, []
        for i, (item, height) in enumerate(zip(self._items, heights)):
            rect = pygame.Rect((win_w - width) // 2, y, width, height)
            self._rects.append(rect)
            color = BUTTON_TEXT if item.enabled else DISABLED_TEXT
            label = font.render(item.label, True, color)
            value = font.render(item.value_text, True, color) if item.kind == "slider" else None
            self._surfaces.append((label, value))
            if item.kind == "slider":
                self._tracks[i] = pygame.Rect(
                    rect.left + self._pad_x, rect.bottom - pad_y - track_h,
                    rect.width - 2 * self._pad_x, track_h,
                )
            y += height + gap

    # -- Interacción ------------------------------------------------------- #
    def index_at(self, pos: Tuple[int, int]) -> Optional[int]:
        for i, rect in enumerate(self._rects):
            if rect.collidepoint(pos):
                return i
        return None

    def hover(self, pos: Tuple[int, int]) -> None:
        index = self.index_at(pos)
        if index is not None and self._items[index].enabled:
            self.focus = index

    def move(self, delta: int) -> None:
        count = len(self._items)
        for step in range(1, count + 1):
            candidate = (self.focus + delta * step) % count
            if self._items[candidate].enabled:
                self.focus = candidate
                return

    def slider_value_at(self, index: int, x: int) -> float:
        """Valor 0..1 de un deslizador para una posición X del ratón."""
        track = self._tracks[index]
        return max(0.0, min(1.0, (x - track.left) / max(1, track.width)))

    # -- Dibujo ------------------------------------------------------------ #
    def draw(self, screen: pygame.Surface) -> None:
        if self._bg_layer is not None:
            screen.blit(self._bg_layer, (0, 0))
        else:
            if not self._dim:
                screen.fill((10, 12, 24))
        if self._dim_surface is not None:
            screen.blit(self._dim_surface, (0, 0))
        for surface, pos in self._title_blits:
            screen.blit(surface, pos)
        for i, (item, rect) in enumerate(zip(self._items, self._rects)):
            draw_button_box(screen, rect, item.enabled, i == self.focus)
            label, value = self._surfaces[i]
            if item.kind == "slider" and value is not None:
                row_y = rect.top + self._pad_y + label.get_height() // 2
                screen.blit(label, label.get_rect(midleft=(rect.left + self._pad_x, row_y)))
                screen.blit(value, value.get_rect(midright=(rect.right - self._pad_x, row_y)))
                self._draw_track(screen, self._tracks[i], item.value, item.enabled)
            else:
                screen.blit(label, label.get_rect(center=rect.center))

    @staticmethod
    def _draw_track(screen: pygame.Surface, track: pygame.Rect, value: float, enabled: bool) -> None:
        radius = track.height // 2
        pygame.draw.rect(screen, (24, 26, 44), track, border_radius=radius)
        filled = pygame.Rect(track.left, track.top, max(track.height, round(track.width * value)), track.height)
        pygame.draw.rect(screen, (120, 160, 240) if enabled else DISABLED_BORDER, filled, border_radius=radius)
        pygame.draw.circle(screen, (255, 255, 255), (min(track.right, filled.right), track.centery), radius + 2)


def draw_toast(
    screen: pygame.Surface, res: ResourceCache, style: TextStyle, text: str, alpha: int
) -> None:
    """Mensaje breve centrado arriba (p. ej. 'Partida guardada')."""
    scale = ui_scale(screen.get_size())
    font = res.font(style.font, max(MIN_FONT_PX, round(style.size * 0.8 * scale)))
    label = font.render(text, True, (255, 255, 255))
    pad = round(14 * scale)
    box = pygame.Surface((label.get_width() + 2 * pad, label.get_height() + pad), pygame.SRCALPHA)
    pygame.draw.rect(box, (20, 20, 40, 235), box.get_rect(), border_radius=pad)
    pygame.draw.rect(box, (255, 255, 255, 120), box.get_rect(), width=2, border_radius=pad)
    box.blit(label, (pad, pad // 2))
    box.set_alpha(max(0, min(255, alpha)))
    screen.blit(box, box.get_rect(midtop=(screen.get_width() // 2, round(14 * scale))))
