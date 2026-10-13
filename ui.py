"""ui.py — Renderizado: fondo, caja de texto con word wrap y botones.

Diseño "responsivo":
  * Las coordenadas y tamaños del JSON están pensados para una resolución de
    DISEÑO de 1280x720. Si la ventana cambia de tamaño, todo se reescala.
  * `UI.prepare()` calcula el layout (fondo escalado, líneas de texto, botones)
    UNA vez por escena/tamaño de ventana; `UI.draw()` sólo pinta lo ya
    calculado, así que cada fotograma es barato.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pygame

from scene import BackgroundStyle, Choice, Scene

log = logging.getLogger(__name__)

BASE_SIZE = (1280, 720)      # resolución de diseño del JSON
MAX_SURFACE = 8192           # límite de seguridad para imágenes escaladas
MIN_FONT_PX = 12             # tamaño mínimo al que se reduce un texto largo

# Tema visual (RGB / RGBA)
PANEL_COLOR = (12, 12, 24, 205)
PANEL_BORDER = (255, 255, 255, 70)
BUTTON_COLOR = (38, 52, 92)
BUTTON_HOVER_COLOR = (70, 96, 168)
BUTTON_BORDER = (200, 210, 240)
BUTTON_TEXT = (255, 255, 255)


# --------------------------------------------------------------------------- #
# Recursos (imágenes y fuentes) con caché
# --------------------------------------------------------------------------- #
class ResourceCache:
    """Carga imágenes y fuentes una sola vez; los fallos avisan y no rompen el juego."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._images: Dict[str, Optional[pygame.Surface]] = {}
        self._fonts: Dict[Tuple[Optional[str], int], pygame.font.Font] = {}
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
        if key not in self._fonts:
            self._fonts[key] = self._load_font(name, size_px)
        return self._fonts[key]

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
# Funciones de layout
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


def place_background(
    bg: BackgroundStyle, image_size: Tuple[int, int], window_size: Tuple[int, int]
) -> pygame.Rect:
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


# --------------------------------------------------------------------------- #
# Botón
# --------------------------------------------------------------------------- #
@dataclass
class Button:
    """Botón clicable ya maquetado. `index` es la posición de la opción."""

    rect: pygame.Rect
    labels: List[pygame.Surface]
    index: int
    line_height: int

    def draw(self, screen: pygame.Surface, hovered: bool) -> None:
        radius = max(4, self.rect.height // 4)
        color = BUTTON_HOVER_COLOR if hovered else BUTTON_COLOR
        pygame.draw.rect(screen, color, self.rect, border_radius=radius)
        pygame.draw.rect(screen, BUTTON_BORDER, self.rect, width=2, border_radius=radius)
        total_h = self.line_height * len(self.labels)
        y = self.rect.centery - total_h // 2
        for label in self.labels:
            screen.blit(label, label.get_rect(midtop=(self.rect.centerx, y)))
            y += self.line_height


# --------------------------------------------------------------------------- #
# Interfaz
# --------------------------------------------------------------------------- #
class UI:
    """Prepara y dibuja la pantalla de una escena."""

    def __init__(self, resources: ResourceCache) -> None:
        self._res = resources
        self._fill: Tuple[int, int, int] = (0, 0, 0)
        self._bg_surface: Optional[pygame.Surface] = None
        self._bg_rect = pygame.Rect(0, 0, 0, 0)
        self._panel_surface: Optional[pygame.Surface] = None
        self._panel_rect = pygame.Rect(0, 0, 0, 0)
        self._text_blits: List[Tuple[pygame.Surface, Tuple[int, int]]] = []
        self._buttons: List[Button] = []

    # -- Preparación (una vez por escena / tamaño de ventana) -------------- #
    def prepare(self, scene: Scene, choices: Sequence[Choice], size: Tuple[int, int]) -> None:
        ui_scale = min(size[0] / BASE_SIZE[0], size[1] / BASE_SIZE[1])
        self._prepare_background(scene.background, size)
        self._prepare_text_box(scene, size, ui_scale)
        self._prepare_buttons(scene, choices, size, ui_scale)

    def _prepare_background(self, bg: BackgroundStyle, size: Tuple[int, int]) -> None:
        self._fill = bg.fill
        self._bg_surface = None
        image = self._res.image(bg.image) if bg.image else None
        if image is None:
            return
        self._bg_rect = place_background(bg, image.get_size(), size)
        self._bg_surface = pygame.transform.smoothscale(image, self._bg_rect.size)

    def _prepare_text_box(self, scene: Scene, size: Tuple[int, int], ui_scale: float) -> None:
        win_w, win_h = size
        style = scene.text_style
        margin = round(16 * ui_scale)
        pad = round(24 * ui_scale)
        panel_w = win_w - 2 * margin
        max_text_w = panel_w - 2 * pad
        min_h, max_h = round(win_h * 0.25), round(win_h * 0.55)

        # Si el texto no cabe, se reduce la fuente poco a poco (hasta un mínimo).
        font_px = max(MIN_FONT_PX, round(style.size * ui_scale))
        while True:
            font = self._res.font(style.font, font_px)
            lines = wrap_text(scene.text, font, max_text_w)
            line_h = font.get_linesize()
            text_h = line_h * len(lines)
            if text_h + 2 * pad <= max_h or font_px <= MIN_FONT_PX:
                break
            font_px -= 2

        panel_h = min(max(min_h, text_h + 2 * pad), win_h - 2 * margin)
        self._panel_rect = pygame.Rect(margin, win_h - margin - panel_h, panel_w, panel_h)

        panel = pygame.Surface(self._panel_rect.size, pygame.SRCALPHA)
        radius = max(6, round(14 * ui_scale))
        pygame.draw.rect(panel, PANEL_COLOR, panel.get_rect(), border_radius=radius)
        pygame.draw.rect(panel, PANEL_BORDER, panel.get_rect(), width=2, border_radius=radius)
        self._panel_surface = panel

        self._text_blits = []
        y = self._panel_rect.top + pad
        for line in lines:
            if line.strip():  # las líneas vacías sólo ocupan espacio
                surface = font.render(line, True, style.color)
                self._text_blits.append((surface, (self._panel_rect.left + pad, y)))
            y += line_h

    def _prepare_buttons(
        self, scene: Scene, choices: Sequence[Choice], size: Tuple[int, int], ui_scale: float
    ) -> None:
        self._buttons = []
        if not choices:
            return
        win_w, _ = size
        style = scene.text_style
        font = self._res.font(style.font, max(MIN_FONT_PX, round(style.size * ui_scale * 0.9)))
        pad_x, pad_y, gap = round(22 * ui_scale), round(10 * ui_scale), round(10 * ui_scale)
        max_btn_w = round(win_w * 0.8)
        line_h = font.get_linesize()

        # Se numeran para poder elegir también con el teclado (1, 2, 3...).
        wrapped = [
            wrap_text(f"{i + 1}. {choice.text}", font, max_btn_w - 2 * pad_x)
            for i, choice in enumerate(choices)
        ]
        text_w = max(font.size(line)[0] for lines in wrapped for line in lines)
        btn_w = min(max_btn_w, text_w + 2 * pad_x)

        # Se apilan hacia arriba desde el borde superior de la caja de texto.
        y = self._panel_rect.top - gap
        for index in reversed(range(len(choices))):
            lines = wrapped[index]
            rect = pygame.Rect(0, 0, btn_w, line_h * len(lines) + 2 * pad_y)
            rect.midbottom = (win_w // 2, y)
            y = rect.top - gap
            labels = [font.render(line, True, BUTTON_TEXT) for line in lines if line]
            self._buttons.insert(0, Button(rect, labels, index, line_h))

    # -- Dibujo y consulta -------------------------------------------------- #
    def draw(self, screen: pygame.Surface, mouse_pos: Tuple[int, int]) -> None:
        screen.fill(self._fill)
        if self._bg_surface is not None:
            screen.blit(self._bg_surface, self._bg_rect)
        if self._panel_surface is not None:
            screen.blit(self._panel_surface, self._panel_rect)
        for surface, pos in self._text_blits:
            screen.blit(surface, pos)
        for button in self._buttons:
            button.draw(screen, button.rect.collidepoint(mouse_pos))

    def button_at(self, pos: Tuple[int, int]) -> Optional[int]:
        """Índice de la opción bajo `pos`, o None si no hay botón ahí."""
        for button in self._buttons:
            if button.rect.collidepoint(pos):
                return button.index
        return None