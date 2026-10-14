"""engine.py — Bucle principal, ventana y pila de estados.

El motor orquesta los demás módulos y no contiene lógica de historia ni de dibujo:
    Story/Scene   -> datos          (scene.py, state.py)
    states.py     -> qué pasa en cada pantalla (título, partida, pausa...)
    UI/MenuView   -> pantalla       (ui.py)
    AudioManager  -> sonido         (audio_manager.py)
    SaveManager   -> guardado       (save_manager.py)
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pygame

from audio_manager import AudioManager
from input_map import Action, translate
from save_manager import SaveError, SaveManager, Slot, UserSettings
from scene import Story
from states import GameState, State, TitleState
from ui import UI, ResourceCache, draw_toast

log = logging.getLogger(__name__)

FPS = 60
RESIZE_SETTLE_MS = 120    # espera tras el último redimensionado antes de recalcular el layout
TOAST_MS = 2200
TOAST_FADE_MS = 400

_RESIZE_EVENTS = (pygame.VIDEORESIZE, pygame.WINDOWRESIZED, pygame.WINDOWSIZECHANGED)
_MOUSE_EVENTS = (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP)


class Engine:
    def __init__(
        self,
        story: Story,
        base_dir: Path,
        saves_dir: Optional[Path] = None,
        story_name: str = "story",
    ) -> None:
        self.story = story
        self.base_dir = Path(base_dir)
        saves_root = Path(saves_dir) if saves_dir else self.base_dir / "saves"
        cfg = story.settings

        pygame.init()
        pygame.display.set_mode((cfg.width, cfg.height), pygame.RESIZABLE)
        pygame.display.set_caption(cfg.title)

        self.clock = pygame.time.Clock()
        self.resources = ResourceCache(self.base_dir)
        self._prefs_path = saves_root / "settings.json"
        self.prefs = UserSettings.load(self._prefs_path)
        self.audio = AudioManager(
            self.base_dir, self.prefs.music_volume, self.prefs.sfx_volume, cfg.music_fade_ms
        )
        self.saves = SaveManager(saves_root / story_name)
        self.ui = UI(self.resources)
        self.menu_style = cfg.default_text_style

        self.running = True
        self.states: List[State] = []
        self._laid_out_size: Optional[Tuple[int, int]] = None
        self._last_resize_ms = 0
        self._toast: Optional[Tuple[str, float]] = None
        self._hand_cursor = False
        self._joysticks: Dict[int, pygame.joystick.JoystickType] = {}
        self._init_joysticks()

        self.push(TitleState(self))

    # -- Propiedades ------------------------------------------------------- #
    @property
    def size(self) -> Tuple[int, int]:
        surface = pygame.display.get_surface()
        assert surface is not None
        return surface.get_size()

    @property
    def is_fullscreen(self) -> bool:
        surface = pygame.display.get_surface()
        return bool(surface and surface.get_flags() & pygame.FULLSCREEN)

    @property
    def laid_out_size(self) -> Optional[Tuple[int, int]]:
        """Tamaño de ventana para el que están calculados los layouts (None antes del primer fotograma)."""
        return self._laid_out_size

    @property
    def top(self) -> Optional[State]:
        return self.states[-1] if self.states else None

    # -- Pila de estados --------------------------------------------------- #
    def push(self, state: State) -> None:
        self.states.append(state)
        state.enter()

    def pop(self) -> None:
        if self.states:
            self.states.pop()
        if self.states:
            self.states[-1].resume()

    def replace_all(self, state: State) -> None:
        self.states.clear()
        self.push(state)

    # -- Acciones de alto nivel (las usan los estados) ---------------------- #
    def start_new_game(self) -> None:
        self.ui.reset()
        self.replace_all(
            GameState(self, self.story.start_id, variables=dict(self.story.settings.variables))
        )

    def load_game(self, slot: Slot) -> bool:
        data = self.saves.read(slot)
        if data is None:
            self.toast("No hay ninguna partida en esa ranura")
            return False
        if data.scene_id not in self.story.scenes:
            self.toast("Esa partida no es compatible con esta historia")
            return False
        self.ui.reset()
        self.replace_all(
            GameState(self, data.scene_id, data.line_index, data.variables, restored=True)
        )
        self.toast("Partida cargada")
        return True

    def continue_game(self) -> None:
        latest = self.saves.latest()
        if latest is not None:
            self.load_game(latest[0])

    def save_game(self, slot: Slot, game: GameState) -> bool:
        scene_id, line_index, variables = game.snapshot()
        try:
            self.saves.save(slot, scene_id, line_index, variables)
        except SaveError as exc:
            log.error("%s", exc)
            self.toast("No se pudo guardar la partida")
            return False
        self.toast(f"Partida guardada (ranura {slot})")
        return True

    def return_to_title(self) -> None:
        self.ui.reset()
        self.replace_all(TitleState(self))

    def save_prefs(self) -> None:
        try:
            self.prefs.save(self._prefs_path)
        except SaveError as exc:
            log.warning("No se pudieron guardar los ajustes: %s", exc)

    def toggle_fullscreen(self) -> None:
        try:
            pygame.display.toggle_fullscreen()
        except pygame.error as exc:   # algunos sistemas/gestores de ventanas no lo permiten
            log.warning("Pantalla completa no disponible: %s", exc)
            self.toast("Pantalla completa no disponible en este sistema")
            return
        self._last_resize_ms = pygame.time.get_ticks()

    def toast(self, text: str) -> None:
        """Muestra un mensaje breve arriba de la pantalla."""
        self._toast = (text, float(TOAST_MS))

    def quit(self) -> None:
        self.running = False

    # -- Mandos ------------------------------------------------------------ #
    def _init_joysticks(self) -> None:
        pygame.joystick.init()
        for index in range(pygame.joystick.get_count()):
            self._add_joystick(index)

    def _add_joystick(self, device_index: int) -> None:
        try:
            joystick = pygame.joystick.Joystick(device_index)
        except pygame.error:
            return
        self._joysticks[joystick.get_instance_id()] = joystick   # hay que conservar la referencia

    # -- Eventos ----------------------------------------------------------- #
    def _handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
            return
        if event.type in _RESIZE_EVENTS:
            self._last_resize_ms = pygame.time.get_ticks()
            return
        if event.type == pygame.JOYDEVICEADDED:
            self._add_joystick(event.device_index)
            return
        if event.type == pygame.JOYDEVICEREMOVED:
            self._joysticks.pop(event.instance_id, None)
            return

        state = self.top
        if state is None:
            return
        inp = translate(event)
        if inp is not None:
            if inp.action is Action.FULLSCREEN:
                self.toggle_fullscreen()
            else:
                state.handle_input(inp)
        elif event.type in _MOUSE_EVENTS:
            state.handle_mouse(event)

    # -- Fotograma --------------------------------------------------------- #
    def _maybe_relayout(self) -> None:
        """Recalcula el layout de todos los estados cuando el tamaño se estabiliza.

        Mientras se arrastra el borde de la ventana llegan decenas de eventos de
        resize; reescalar imágenes en cada uno haría ir a tirones, así que se
        espera a que pasen RESIZE_SETTLE_MS sin cambios.
        """
        size = self.size
        if size == self._laid_out_size:
            return
        settling = pygame.time.get_ticks() - self._last_resize_ms < RESIZE_SETTLE_MS
        if self._laid_out_size is not None and settling:
            return
        for state in self.states:
            state.relayout(size)
        self._laid_out_size = size

    def _update_cursor(self, state: Optional[State]) -> None:
        wants = state is not None and state.wants_hand(pygame.mouse.get_pos())
        if wants != self._hand_cursor:
            self._hand_cursor = wants
            with contextlib.suppress(pygame.error):   # algunos entornos no admiten cursores del sistema
                pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND if wants else pygame.SYSTEM_CURSOR_ARROW)

    def _draw(self, screen: pygame.Surface) -> None:
        # Se dibuja desde el último estado opaco hacia arriba (los transparentes se superponen).
        start = 0
        for i in range(len(self.states) - 1, -1, -1):
            if not self.states[i].transparent:
                start = i
                break
        visible = self.states[start:]
        top_menu = max((i for i, s in enumerate(visible) if s.is_menu), default=-1)
        for i, state in enumerate(visible):
            if state.is_menu and i != top_menu:
                continue   # un menú tapa a los menús de debajo (p. ej. Ajustes sobre Pausa)
            state.draw(screen)
        if self._toast is not None:
            text, remaining = self._toast
            alpha = round(255 * min(1.0, remaining / TOAST_FADE_MS))
            draw_toast(screen, self.resources, self.menu_style, text, alpha)

    def tick(self) -> None:
        """Un fotograma: eventos -> actualización -> dibujo."""
        for event in pygame.event.get():
            self._handle_event(event)

        dt_ms = self.clock.tick(FPS)
        self.audio.update(dt_ms)
        if self._toast is not None:
            text, remaining = self._toast
            self._toast = (text, remaining - dt_ms) if remaining - dt_ms > 0 else None

        self._maybe_relayout()
        state = self.top
        if state is not None:
            state.update(dt_ms)
        self._update_cursor(state)

        screen = pygame.display.get_surface()
        assert screen is not None
        self._draw(screen)
        pygame.display.flip()

    def run(self) -> None:
        try:
            while self.running:
                self.tick()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.save_prefs()
        self.audio.shutdown()
        pygame.quit()
