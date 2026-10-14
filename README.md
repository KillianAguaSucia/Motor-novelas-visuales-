# Motor de Novela Visual (Pygame)

## Ejecutar
```bash
pip install -r requirements.txt
python main.py                  # carga data/story.json
python main.py otra_historia.json
python main.py --check          # valida historia y recursos sin abrir ventana
python main.py --strict         # no arranca si falta algún archivo
```
> **Copia siempre la carpeta entera.** Los módulos dependen unos de otros
> (`errors.py`, `state.py`, `save_manager.py`, `input_map.py`, `states.py`...); si falta uno,
> Python dirá `No module named '...'`.

**Controles:** clic / `Espacio` / `Enter` avanzan el texto y eligen opción · `↑ ↓` y `1`-`9` para elegir ·
`Esc` o clic derecho: pausa · `F5` guardado rápido · `F9` carga rápida · `F11` pantalla completa ·
Mando: cruceta, `A` aceptar, `B`/`Start` atrás (sin probar con hardware real).

## Estructura
```
main.py            punto de entrada (argumentos, validación)
engine.py          bucle, ventana, pila de estados, guardado
states.py          pantallas: título, partida, pausa, ranuras, ajustes
ui.py              fondo, sprites, diálogo (paginado + máquina de escribir), botones, menús
scene.py           modelo de datos y validación del JSON (sin Pygame)
state.py           variables, condiciones y efectos (sin Pygame)
save_manager.py    partidas y preferencias (JSON, escritura atómica)
audio_manager.py   música con fundidos + efectos
input_map.py       teclado/mando -> acciones
errors.py          StoryError
data/story.json    la historia
assets/            fonts/ images/ music/ sfx/
tools/make_assets.py   regenera los recursos de ejemplo
tests/             pytest (python -m pytest)
```

## Formato de una escena
| Clave | Descripción |
|---|---|
| `id` | Identificador único |
| `text` **o** `dialogue` | Un texto, o una lista de líneas. Cada línea es un texto o `{text, speaker, speaker_color, text_style, sfx, set, requires}` |
| `text_style` | `color` (`"#RRGGBB"`/`[r,g,b]`), `size` (px a 1280x720), `font` (ruta `.ttf`/`.otf` o fuente del sistema) |
| `background` | `image`, `fit` (`cover`/`contain`/`none`), `scale`, `x`, `y`, `anchor`, `fill` |
| `characters` | Lista de sprites: `image`, `position` (`left`/`center`/`right`) o `x`,`y`, `anchor`, `height` o `scale`, `flip`, `requires` |
| `music`, `music_volume` | Pista en bucle (no se reinicia si es la misma) y su volumen 0..1 |
| `sfx` | Efecto que suena una vez al entrar |
| `transition` | `"cut"`, `"fade"` o `{"type": "fade", "ms": 900}` (fundido entre escenas) |
| `on_enter` | Efectos sobre variables al entrar |
| `choices` | `{text, target, requires, set, if_locked ("hide"/"disable"), locked_hint}`. Vacía = final |

### Variables
```json
"set":      { "tiene_mapa": true, "valor": { "add": 1 } }
"requires": { "tiene_mapa": true, "valor": { ">=": 2 } }
```
Todas las condiciones de un `requires` deben cumplirse. Los textos muestran variables con `{valor}`.
Los valores iniciales de una partida nueva van en `settings.variables`.

### `settings`
`window` (`width`, `height`, `title`), `default_text_style`, `restart_text`, `music_fade_ms`,
`default_transition_ms`, `title_background`, `title_music`, `variables`.

Las partidas se guardan en `saves/<historia>/` y las preferencias en `saves/settings.json`.

## Desarrollo
```bash
pip install -r requirements-dev.txt
python -m pytest
ruff check .
mypy --python-executable "$(which python3)"
```
