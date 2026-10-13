"""engine.py — Bucle principal: eventos, navegación entre escenas y render.

El motor orquesta los demás módulos y no contiene lógica de dibujo ni de audio:
    Story/Scene  -> datos          (scene.py)
    UI           -> pantalla       (ui.py)
    AudioManager -> música         (audio_manager.py)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

import pygame

from audio_manager import AudioManager
from scene import Choice, Scene, Story
from ui import UI, ResourceCache

log = logging.getLogger(__name__)

FPS = 60
DEFAULT_WINDOW = {"width": 1280, "height": 720, "title": "Novela Visual"}

# Teclas 1..9 (fila superior y teclado numérico) -> índice de opción 0..8
_NUMBER_KEYS = {
    **{pygame.K_1 + i: i for i in range(9)},
    **{pygame.K_KP1 + i: i for i in range(9)},
}


class Engine:
    def __init__(self, story_path: Path, base_dir: Path) -> None:
        self.story = Story.load(story_path)
        settings = self.story.settings

        pygame.init()
        window = {**DEFAULT_WINDOW, **settings.get("window", {})}
        self.screen = pygame.display.set_mode(
            (int(window["width"]), int(window["height"])), pygame.RESIZABLE
        )
        pygame.display.set_caption(str(window["title"]))

        self.clock = pygame.time.Clock()
        self.audio = AudioManager(base_dir, fade_ms=int(settings.get("music_fade_ms", 800)))
        self.ui = UI(ResourceCache(base_dir))
        self.restart_text = str(settings.get("restart_text", "Volver a empezar"))

        self.running = True
        self.scene: Scene = self.story.start
        self._choices: Sequence[Choice] = ()
        self._layout_key: Optional[tuple] = None   # (id de escena, tamaño de ventana)
        self._hand_cursor = False
        self.goto(self.story.start_id)

    # -- Navegación ---------------------------------------------------------- #
    def goto(self, scene_id: str) -> None:
        """Cambia a otra escena: actualiza la música y fuerza recalcular el layout."""
        self.scene = self.story.get(scene_id)
        # Un final (sin opciones) ofrece reiniciar la historia.
        self._choices = self.scene.choices or (Choice(self.restart_text, self.story.start_id),)
        self.audio.play(self.scene.music)
        self._layout_key = None
        log.info("Escena: %s", self.scene.id)

    def choose(self, index: int) -> None:
        """Sigue la opción número `index` de la escena actual (si existe)."""
        if 0 <= index < len(self._choices):
            self.goto(self._choices[index].target)

    # -- Eventos ------------------------------------------------------------- #
    def _handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.running = False
            elif event.key == pygame.K_F11:
                pygame.display.toggle_fullscreen()
            elif event.key in _NUMBER_KEYS:
                self.choose(_NUMBER_KEYS[event.key])
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            index = self.ui.button_at(event.pos)
            if index is not None:
                self.choose(index)

    def _update_cursor(self, mouse_pos: tuple) -> None:
        """Cursor de mano sobre los botones."""
        over_button = self.ui.button_at(mouse_pos) is not None
        if over_button != self._hand_cursor:
            self._hand_cursor = over_button
            try:
                pygame.mouse.set_cursor(
                    pygame.SYSTEM_CURSOR_HAND if over_button else pygame.SYSTEM_CURSOR_ARROW
                )
            except pygame.error:
                pass  # algunos entornos no admiten cursores del sistema

    # -- Bucle --------------------------------------------------------------- #
    def tick(self) -> None:
        """Un fotograma: eventos -> actualización -> dibujo."""
        for event in pygame.event.get():
            self._handle_event(event)

        dt_ms = self.clock.tick(FPS)
        self.audio.update(dt_ms)

        screen = pygame.display.get_surface()   # se actualiza solo al redimensionar
        size = screen.get_size()
        key = (self.scene.id, size)
        if key != self._layout_key:             # nueva escena o ventana redimensionada
            self.ui.prepare(self.scene, self._choices, size)
            self._layout_key = key

        mouse_pos = pygame.mouse.get_pos()
        self._update_cursor(mouse_pos)
        self.ui.draw(screen, mouse_pos)
        pygame.display.flip()

    def run(self) -> None:
        try:
            while self.running:
                self.tick()
        finally:
            self.audio.shutdown()
            pygame.quit()