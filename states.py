"""states.py — Estados del juego: título, partida, pausa, ranuras y ajustes.

El motor mantiene una PILA de estados; sólo el de arriba recibe entrada y se
actualiza. Los estados "transparentes" (pausa, y ajustes/ranuras abiertos desde
la pausa) dejan ver y atenúan lo que hay debajo.

    TitleState ─▶ SettingsState / SlotsState
    GameState  ─▶ PauseState ─▶ SettingsState / SlotsState
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

import pygame

from input_map import Action, Input
from save_manager import AUTO, SaveError, Slot
from scene import Choice, DialogueLine
from state import GameVars
from ui import ChoiceItem, MenuItem, MenuView

if TYPE_CHECKING:
    from engine import Engine

log = logging.getLogger(__name__)

# Textos de la interfaz (en un único sitio por si se quiere traducir)
T_NEW_GAME = "Nueva partida"
T_CONTINUE = "Continuar"
T_LOAD = "Cargar partida"
T_SAVE = "Guardar partida"
T_SETTINGS = "Ajustes"
T_QUIT = "Salir"
T_PAUSE = "Pausa"
T_RESUME = "Reanudar"
T_MAIN_MENU = "Menú principal"
T_BACK = "Volver"
T_EMPTY_SLOT = "vacía"
SLIDER_STEP = 0.05


class State:
    """Estado base. Los métodos vacíos permiten sobrescribir sólo lo necesario."""

    transparent = False   # True: se dibuja también el estado de debajo
    is_menu = False       # True: sólo se ve el menú más alto de la pila (no se apilan visualmente)

    def __init__(self, app: "Engine") -> None:
        self.app = app

    def enter(self) -> None:
        """Se llama al apilar el estado."""

    def resume(self) -> None:
        """Se llama cuando el estado de encima se retira y éste vuelve a ser el activo."""

    def handle_input(self, inp: Input) -> None:
        """Acción de teclado o mando."""

    def handle_mouse(self, event: pygame.event.Event) -> None:
        """Evento de ratón (MOUSEMOTION / MOUSEBUTTONDOWN / MOUSEBUTTONUP)."""

    def update(self, dt_ms: float) -> None:
        """Avanza la lógica un fotograma."""

    def draw(self, screen: pygame.Surface) -> None:
        """Dibuja el estado."""

    def relayout(self, size: Tuple[int, int]) -> None:
        """La ventana cambió de tamaño."""

    def wants_hand(self, pos: Tuple[int, int]) -> bool:
        """True si hay algo clicable bajo el ratón (cursor de mano)."""
        return False


# --------------------------------------------------------------------------- #
# Menús
# --------------------------------------------------------------------------- #
class MenuState(State):
    """Base de los menús: navegación con teclado/mando/ratón y deslizadores."""

    is_menu = True

    def __init__(
        self, app: "Engine", title: str = "", overlay: bool = False, with_background: bool = False
    ) -> None:
        super().__init__(app)
        self.transparent = overlay
        background = app.story.settings.title_background if with_background else None
        self.view = MenuView(app.resources, app.menu_style, title, background, dim=overlay)
        self._dragging: Optional[int] = None

    # -- A sobrescribir ---------------------------------------------------- #
    def build_items(self) -> List[MenuItem]:
        return []

    def activate(self, index: int) -> None:
        """Se eligió el botón `index`."""

    def back(self) -> None:
        """Se pulsó Escape / botón de atrás."""

    def set_slider(self, index: int, value: float) -> None:
        """Se movió el deslizador `index` a `value` (0..1)."""

    # -- Ciclo de vida ----------------------------------------------------- #
    def refresh(self) -> None:
        self.view.set_items(self.build_items())
        self.view.layout(self.app.size)

    def enter(self) -> None:
        self.refresh()

    def resume(self) -> None:
        self.refresh()

    def relayout(self, size: Tuple[int, int]) -> None:
        self.view.layout(size)

    # -- Entrada ----------------------------------------------------------- #
    def handle_input(self, inp: Input) -> None:
        view, action = self.view, inp.action
        if action is Action.UP:
            view.move(-1)
        elif action is Action.DOWN:
            view.move(1)
        elif action is Action.BACK:
            self.back()
        elif view.items and action is Action.CONFIRM:
            if view.items[view.focus].enabled and view.items[view.focus].kind == "button":
                self.activate(view.focus)
        elif view.items and action in (Action.LEFT, Action.RIGHT):
            item = view.items[view.focus]
            if item.kind == "slider" and item.enabled:
                delta = SLIDER_STEP if action is Action.RIGHT else -SLIDER_STEP
                self.set_slider(view.focus, max(0.0, min(1.0, round(item.value + delta, 2))))
                self.refresh()

    def handle_mouse(self, event: pygame.event.Event) -> None:
        view = self.view
        if event.type == pygame.MOUSEMOTION:
            if self._dragging is not None:
                self._drag(event.pos[0])
            else:
                view.hover(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            index = view.index_at(event.pos)
            if index is None or not view.items[index].enabled:
                return
            view.focus = index
            if view.items[index].kind == "slider":
                self._dragging = index
                self._drag(event.pos[0])
            else:
                self.activate(index)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._dragging = None

    def _drag(self, x: int) -> None:
        if self._dragging is not None:
            self.set_slider(self._dragging, round(self.view.slider_value_at(self._dragging, x), 2))
            self.refresh()

    def wants_hand(self, pos: Tuple[int, int]) -> bool:
        index = self.view.index_at(pos)
        return index is not None and self.view.items[index].enabled

    def draw(self, screen: pygame.Surface) -> None:
        self.view.draw(screen)


class ActionMenuState(MenuState):
    """Menú de botones donde cada uno ejecuta una función."""

    def __init__(self, app: "Engine", title: str = "", overlay: bool = False, with_background: bool = False) -> None:
        super().__init__(app, title, overlay, with_background)
        self._actions: List[Callable[[], None]] = []

    def entries(self) -> List[Tuple[MenuItem, Callable[[], None]]]:
        return []

    def build_items(self) -> List[MenuItem]:
        entries = self.entries()
        self._actions = [action for _, action in entries]
        return [item for item, _ in entries]

    def activate(self, index: int) -> None:
        if 0 <= index < len(self._actions):
            self._actions[index]()


class TitleState(ActionMenuState):
    def __init__(self, app: "Engine") -> None:
        settings = app.story.settings
        super().__init__(app, settings.title, overlay=False, with_background=True)

    def enter(self) -> None:
        self.app.audio.play(self.app.story.settings.title_music)
        super().enter()

    def entries(self) -> List[Tuple[MenuItem, Callable[[], None]]]:
        has_save = self.app.saves.latest() is not None
        app = self.app
        return [
            (MenuItem(T_NEW_GAME), app.start_new_game),
            (MenuItem(T_CONTINUE, enabled=has_save), app.continue_game),
            (MenuItem(T_LOAD, enabled=has_save), lambda: app.push(SlotsState(app, "load", overlay=False))),
            (MenuItem(T_SETTINGS), lambda: app.push(SettingsState(app, overlay=False))),
            (MenuItem(T_QUIT), app.quit),
        ]


class PauseState(ActionMenuState):
    def __init__(self, app: "Engine", game: "GameState") -> None:
        super().__init__(app, T_PAUSE, overlay=True)
        self.game = game

    def entries(self) -> List[Tuple[MenuItem, Callable[[], None]]]:
        app = self.app
        return [
            (MenuItem(T_RESUME), app.pop),
            (MenuItem(T_SAVE), lambda: app.push(SlotsState(app, "save", overlay=True, game=self.game))),
            (MenuItem(T_LOAD, enabled=app.saves.latest() is not None),
             lambda: app.push(SlotsState(app, "load", overlay=True))),
            (MenuItem(T_SETTINGS), lambda: app.push(SettingsState(app, overlay=True))),
            (MenuItem(T_MAIN_MENU), app.return_to_title),
        ]

    def back(self) -> None:
        self.app.pop()


class SlotsState(MenuState):
    """Elegir ranura para guardar o cargar."""

    def __init__(
        self, app: "Engine", mode: str, overlay: bool, game: Optional["GameState"] = None
    ) -> None:
        super().__init__(app, T_SAVE if mode == "save" else T_LOAD, overlay, with_background=not overlay)
        self.mode = mode
        self.game = game
        self._slots: List[Slot] = []

    def build_items(self) -> List[MenuItem]:
        saves = self.app.saves
        slots: List[Slot] = list(saves.slot_ids)
        if self.mode == "load":
            slots.append(AUTO)   # el autoguardado sólo se puede cargar
        self._slots = slots
        items = []
        for slot in slots:
            data = saves.read(slot)
            name = "Autoguardado" if slot == AUTO else f"Ranura {slot}"
            info = f"{data.scene_id} · {data.when}" if data else T_EMPTY_SLOT
            enabled = self.mode == "save" or data is not None
            items.append(MenuItem(f"{name}: {info}", enabled=enabled))
        items.append(MenuItem(T_BACK))
        return items

    def activate(self, index: int) -> None:
        if index >= len(self._slots):   # "Volver"
            self.back()
            return
        slot = self._slots[index]
        if self.mode == "save":
            if self.game is not None and self.app.save_game(slot, self.game):
                self.app.pop()
        else:
            self.app.load_game(slot)

    def back(self) -> None:
        self.app.pop()


class SettingsState(MenuState):
    """Volúmenes, velocidad del texto y pantalla completa."""

    def __init__(self, app: "Engine", overlay: bool) -> None:
        super().__init__(app, T_SETTINGS, overlay, with_background=not overlay)

    def build_items(self) -> List[MenuItem]:
        prefs = self.app.prefs
        cps = prefs.typing_cps
        speed_text = "Instantáneo" if cps <= 0 else f"{cps:.0f} car/s"
        fullscreen = "Sí" if self.app.is_fullscreen else "No"
        return [
            MenuItem("Música", "slider", value=prefs.music_volume, value_text=f"{prefs.music_volume:.0%}"),
            MenuItem("Efectos", "slider", value=prefs.sfx_volume, value_text=f"{prefs.sfx_volume:.0%}"),
            MenuItem("Velocidad del texto", "slider", value=prefs.text_speed, value_text=speed_text),
            MenuItem(f"Pantalla completa: {fullscreen}"),
            MenuItem(T_BACK),
        ]

    def set_slider(self, index: int, value: float) -> None:
        prefs, audio = self.app.prefs, self.app.audio
        if index == 0:
            prefs.music_volume = value
            audio.set_music_volume(value)
        elif index == 1:
            prefs.sfx_volume = value
            audio.set_sfx_volume(value)
        elif index == 2:
            prefs.text_speed = value

    def activate(self, index: int) -> None:
        if index == 3:
            self.app.toggle_fullscreen()
            self.refresh()
        elif index == 4:
            self.back()

    def back(self) -> None:
        self.app.save_prefs()
        self.app.pop()


# --------------------------------------------------------------------------- #
# Partida
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Option:
    """Una opción mostrada al jugador al terminar una escena."""

    text: str
    enabled: bool = True
    choice: Optional[Choice] = None
    kind: str = "choice"   # "choice" | "restart" | "menu"


class GameState(State):
    """Reproduce la historia: avanza líneas, aplica efectos y sigue las opciones."""

    def __init__(
        self,
        app: "Engine",
        scene_id: str,
        line_index: int = 0,
        variables: Optional[dict] = None,
        restored: bool = False,
    ) -> None:
        super().__init__(app)
        self.vars = GameVars(variables)
        self.scene = app.story.get(scene_id)
        self.line_index = line_index
        self._restored = restored
        self._options: List[Option] = []

    # -- Entrada en escena ------------------------------------------------- #
    def enter(self) -> None:
        if self._restored:
            self._enter_scene(self.scene.id, self.line_index, fresh=False)
        else:
            self._enter_scene(self.scene.id)

    def _enter_scene(self, scene_id: str, line_index: Optional[int] = None, fresh: bool = True) -> None:
        """Cambia de escena. `fresh=False` (al cargar partida) no repite efectos ni sonidos."""
        app, story = self.app, self.app.story
        self.scene = scene = story.get(scene_id)
        if fresh:
            self.vars.apply(scene.on_enter)
        sprites = [s for s in scene.sprites if self.vars.satisfied(s.requires)]
        fade_ms = scene.transition_ms if scene.transition_ms is not None else story.settings.default_transition_ms
        app.audio.play(scene.music, scene.music_volume)
        if fresh:
            app.audio.play_sfx(scene.sfx)
        app.ui.set_scene(scene, sprites, app.size, fade_ms if fresh else 0)

        if line_index is not None and 0 <= line_index < len(scene.lines):
            first: Optional[int] = line_index
        else:
            first = self._find_line(0)
        self._show_line(first, apply_effects=fresh)
        if fresh:
            self._autosave()
        log.info("Escena: %s", scene.id)

    def _find_line(self, start: int) -> Optional[int]:
        """Primera línea desde `start` cuyas condiciones se cumplen (o None)."""
        for i in range(start, len(self.scene.lines)):
            if self.vars.satisfied(self.scene.lines[i].requires):
                return i
        return None

    def _show_line(self, index: Optional[int], apply_effects: bool = True) -> None:
        scene, app = self.scene, self.app
        if index is None:   # ninguna línea aplicable: se muestra la escena vacía y sus opciones
            line = DialogueLine(text="", style=scene.text_style)
            self.line_index = 0
        else:
            line = scene.lines[index]
            self.line_index = index
            if apply_effects:
                self.vars.apply(line.set)
                app.audio.play_sfx(line.sfx)
        app.ui.set_line(line, self.vars.render(line.text))
        has_more = index is not None and self._find_line(index + 1) is not None
        self._refresh_choices(has_more)

    def _refresh_choices(self, has_more: bool) -> None:
        if has_more:
            self._options = []
        else:
            self._options = self._build_options()
        self.app.ui.set_choices([ChoiceItem(o.text, o.enabled) for o in self._options])

    def _build_options(self) -> List[Option]:
        scene = self.scene
        options: List[Option] = []
        for choice in scene.choices:
            if self.vars.satisfied(choice.requires):
                options.append(Option(choice.text, True, choice))
            elif choice.if_locked == "disable":
                hint = f" {choice.locked_hint}" if choice.locked_hint else ""
                options.append(Option(choice.text + hint, False, choice))
        if not any(o.enabled for o in options):
            if scene.choices:
                log.warning("Escena '%s': ninguna opción disponible con las variables actuales", scene.id)
            return [
                Option(self.app.story.settings.restart_text, True, None, "restart"),
                Option(T_MAIN_MENU, True, None, "menu"),
            ]
        return options

    # -- Acciones ---------------------------------------------------------- #
    def _advance(self) -> None:
        """Completa el texto, pasa de página o de línea (por este orden)."""
        ui = self.app.ui
        if ui.typing:
            ui.skip_typing()
        elif ui.has_next_page:
            ui.next_page()
        else:
            nxt = self._find_line(self.line_index + 1)
            if nxt is not None:
                self._show_line(nxt)

    def _choose(self, position: int) -> None:
        if not 0 <= position < len(self._options):
            return
        option = self._options[position]
        if not option.enabled:
            return
        if option.kind == "restart":
            self.app.start_new_game()
        elif option.kind == "menu":
            self.app.return_to_title()
        elif option.choice is not None:
            self.vars.apply(option.choice.set)
            self._enter_scene(option.choice.target)

    def _autosave(self) -> None:
        try:
            scene_id, line_index, variables = self.snapshot()
            self.app.saves.save(AUTO, scene_id, line_index, variables)
        except SaveError as exc:
            log.warning("Autoguardado fallido: %s", exc)

    @property
    def options(self) -> Tuple[Option, ...]:
        """Opciones disponibles ahora mismo (vacío mientras queden líneas por leer)."""
        return tuple(self._options)

    def snapshot(self) -> Tuple[str, int, dict]:
        """Estado actual para guardarlo: (escena, línea, variables)."""
        return self.scene.id, self.line_index, self.vars.snapshot()

    # -- Entrada ----------------------------------------------------------- #
    def handle_input(self, inp: Input) -> None:
        ui, action = self.app.ui, inp.action
        if action is Action.BACK:
            self.app.push(PauseState(self.app, self))
        elif action is Action.QUICKSAVE:
            self.app.save_game(1, self)
        elif action is Action.QUICKLOAD:
            self.app.load_game(1)
        elif ui.choices_visible:
            if action in (Action.UP, Action.LEFT):
                ui.move_focus(-1)
            elif action in (Action.DOWN, Action.RIGHT):
                ui.move_focus(1)
            elif action is Action.CONFIRM:
                self._choose(ui.focus)
            elif action is Action.NUMBER:
                self._choose(inp.index)
        elif action is Action.CONFIRM:
            self._advance()

    def handle_mouse(self, event: pygame.event.Event) -> None:
        ui = self.app.ui
        if event.type == pygame.MOUSEMOTION:
            ui.hover(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 3:
                self.app.push(PauseState(self.app, self))
            elif event.button == 1:
                if ui.choices_visible:
                    index = ui.button_at(event.pos)
                    if index is not None:
                        self._choose(index)
                else:
                    self._advance()

    def wants_hand(self, pos: Tuple[int, int]) -> bool:
        index = self.app.ui.button_at(pos)
        return index is not None and self.app.ui.is_enabled(index)

    # -- Fotograma --------------------------------------------------------- #
    def update(self, dt_ms: float) -> None:
        self.app.ui.update(dt_ms, self.app.prefs.typing_cps)

    def draw(self, screen: pygame.Surface) -> None:
        self.app.ui.draw(screen)

    def relayout(self, size: Tuple[int, int]) -> None:
        self.app.ui.relayout(size)


__all__ = [
    "State", "MenuState", "TitleState", "PauseState", "SlotsState", "SettingsState",
    "GameState", "Option", "MenuView",
]
