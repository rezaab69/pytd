"""Named states for multi-step conversations."""

from __future__ import annotations

from typing import Any

__all__ = ["State", "StatesGroup"]


class State:
    """One step in a conversation.

    Declared inside a :class:`StatesGroup`, a state names itself after the
    attribute it was assigned to, qualified by its group -- so the stored value
    is ``"Form:name"`` rather than a bare ``"name"`` that two unrelated
    conversations could collide on.

    Example:
        >>> class Form(StatesGroup):
        ...     name = State()
        ...     age = State()
        >>> Form.name.name
        'Form:name'
    """

    __slots__ = ("_name",)

    def __init__(self, name: str | None = None) -> None:
        self._name = name

    def __set_name__(self, owner: type, attribute: str) -> None:
        if self._name is None:
            self._name = f"{owner.__name__}:{attribute}"

    @property
    def name(self) -> str:
        """The value written to storage."""
        if self._name is None:
            raise RuntimeError("an unnamed State must live in a StatesGroup or be given a name")
        return self._name

    def __eq__(self, other: object) -> bool:
        if isinstance(other, State):
            return self._name == other._name
        if isinstance(other, str):
            return self._name == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._name)

    def __repr__(self) -> str:
        return f"State({self._name!r})"

    def __str__(self) -> str:
        return self.name


class StatesGroup:
    """A namespace of related :class:`State` objects.

    Subclass it and assign states; they take their qualified names from the
    class. The group itself is never instantiated.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    @classmethod
    def states(cls) -> tuple[State, ...]:
        """Every state declared on this group, in declaration order."""
        return tuple(value for value in vars(cls).values() if isinstance(value, State))

    @classmethod
    def names(cls) -> tuple[str, ...]:
        return tuple(state.name for state in cls.states())
