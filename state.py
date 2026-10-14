"""state.py — Variables de partida, condiciones y efectos (sin Pygame).

Permite que la historia "recuerde" lo que ha pasado:

    "set":      {"tiene_mapa": true, "valor": {"add": 1}}     # efectos
    "requires": {"tiene_mapa": true, "valor": {">=": 2}}      # condiciones (todas deben cumplirse)

Las variables que nunca se han asignado valen False / 0 / "" según el tipo con el
que se comparen. Los textos pueden mostrar variables con `{nombre}`.
"""
from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple, Union

from errors import StoryError

Value = Union[bool, int, float, str]

_OPS: Dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _is_value(value: Any) -> bool:
    return isinstance(value, (bool, int, float, str))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _default_for(value: Value) -> Value:
    """Valor de una variable sin asignar, según el tipo con el que se compara."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0
    return ""


@dataclass(frozen=True)
class Condition:
    """Una comparación `variable <op> valor`."""

    var: str
    op: str
    value: Value

    def holds(self, variables: Mapping[str, Value]) -> bool:
        current = variables.get(self.var, _default_for(self.value))
        try:
            return bool(_OPS[self.op](current, self.value))
        except TypeError:  # p. ej. comparar un texto con un número
            return False


@dataclass(frozen=True)
class Effect:
    """Un cambio de variable: asignar (`set`) o sumar (`add`)."""

    var: str
    kind: str  # "set" | "add"
    value: Value


def parse_requires(data: Any) -> Tuple[Condition, ...]:
    """Convierte el JSON de `requires` en condiciones."""
    if data is None:
        return ()
    if not isinstance(data, dict):
        raise StoryError(f"'requires' debe ser un objeto {{variable: valor}}, no {data!r}")
    conditions = []
    for var, spec in data.items():
        if isinstance(spec, dict):
            if not spec:
                raise StoryError(f"'requires': la condición de '{var}' está vacía")
            for op, value in spec.items():
                if op not in _OPS:
                    raise StoryError(
                        f"'requires': operador desconocido '{op}' en '{var}' (usa {sorted(_OPS)})"
                    )
                if not _is_value(value):
                    raise StoryError(f"'requires': valor inválido para '{var}' {op}: {value!r}")
                conditions.append(Condition(var, op, value))
        elif _is_value(spec):
            conditions.append(Condition(var, "==", spec))
        else:
            raise StoryError(f"'requires': valor inválido para '{var}': {spec!r}")
    return tuple(conditions)


def parse_effects(data: Any) -> Tuple[Effect, ...]:
    """Convierte el JSON de `set` / `on_enter` en efectos."""
    if data is None:
        return ()
    if not isinstance(data, dict):
        raise StoryError(f"'set' debe ser un objeto {{variable: valor}}, no {data!r}")
    effects = []
    for var, spec in data.items():
        if isinstance(spec, dict):
            if set(spec) != {"add"} or not _is_number(spec["add"]):
                raise StoryError(f"'set': '{var}' sólo admite {{\"add\": número}}, no {spec!r}")
            effects.append(Effect(var, "add", spec["add"]))
        elif _is_value(spec):
            effects.append(Effect(var, "set", spec))
        else:
            raise StoryError(f"'set': valor inválido para '{var}': {spec!r}")
    return tuple(effects)


class GameVars:
    """Variables de la partida en curso."""

    def __init__(self, values: Optional[Mapping[str, Value]] = None) -> None:
        self.values: Dict[str, Value] = dict(values or {})

    def satisfied(self, conditions: Tuple[Condition, ...]) -> bool:
        return all(c.holds(self.values) for c in conditions)

    def apply(self, effects: Tuple[Effect, ...]) -> None:
        for effect in effects:
            if effect.kind == "set":
                self.values[effect.var] = effect.value
            else:  # add
                current = self.values.get(effect.var, 0)
                if not _is_number(current):
                    current = 0
                self.values[effect.var] = current + effect.value  # type: ignore[operator]

    def render(self, text: str) -> str:
        """Sustituye `{variable}` por su valor (las desconocidas se dejan tal cual)."""

        def replace(match: "re.Match[str]") -> str:
            name = match.group(1)
            if name not in self.values:
                return match.group(0)
            value = self.values[name]
            if isinstance(value, bool):
                return "sí" if value else "no"
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)

        return _PLACEHOLDER.sub(replace, text)

    def snapshot(self) -> Dict[str, Value]:
        return dict(self.values)
