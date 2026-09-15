"""FSMContext and declarative states."""

from __future__ import annotations

import pytest

from tdpy.fsm import FSMContext, MemoryStorage, State, StatesGroup, StorageKey

KEY = StorageKey(chat_id=100, user_id=7)


class Form(StatesGroup):
    name = State()
    age = State()


@pytest.fixture
def fsm() -> FSMContext:
    return FSMContext(MemoryStorage(), KEY)


async def test_state_starts_unset(fsm: FSMContext) -> None:
    assert await fsm.get_state() is None


async def test_a_state_object_is_stored_by_name(fsm: FSMContext) -> None:
    await fsm.set_state(Form.name)

    assert await fsm.get_state() == "Form:name"


async def test_a_plain_string_works_too(fsm: FSMContext) -> None:
    await fsm.set_state("custom")

    assert await fsm.get_state() == "custom"


async def test_update_data_merges(fsm: FSMContext) -> None:
    await fsm.update_data(name="Ada")
    result = await fsm.update_data(age=36)

    assert result == {"name": "Ada", "age": 36}
    assert await fsm.get_data() == {"name": "Ada", "age": 36}


async def test_set_data_replaces(fsm: FSMContext) -> None:
    await fsm.update_data(name="Ada")
    await fsm.set_data({"age": 36})

    assert await fsm.get_data() == {"age": 36}


async def test_clear_ends_the_conversation(fsm: FSMContext) -> None:
    await fsm.set_state(Form.age)
    await fsm.update_data(name="Ada")

    await fsm.clear()

    assert await fsm.get_state() is None
    assert await fsm.get_data() == {}


def test_states_take_qualified_names() -> None:
    # A bare "name" would collide with every other conversation's first step.
    assert Form.name.name == "Form:name"
    assert Form.names() == ("Form:name", "Form:age")


def test_a_state_compares_to_its_stored_value() -> None:
    assert Form.name == "Form:name"
    assert Form.name == State("Form:name")
    assert Form.name != Form.age
    assert Form.name != 42


def test_states_are_hashable() -> None:
    assert len({Form.name, Form.age, State("Form:name")}) == 2


def test_an_explicit_name_is_kept() -> None:
    class Other(StatesGroup):
        step = State("legacy-name")

    assert Other.step.name == "legacy-name"


def test_an_unnamed_state_outside_a_group_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="StatesGroup"):
        _ = State().name


def test_str_and_repr() -> None:
    assert str(Form.name) == "Form:name"
    assert repr(Form.name) == "State('Form:name')"
