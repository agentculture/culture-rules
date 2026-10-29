"""keyset.filter and keyset.add are registered on the node and have describe phrases (t4)."""

from culture_rules.node.actions.keyset import (
    KEYSET_ADD_BUILTIN,
    KEYSET_FILTER_BUILTIN,
    KeysetAddPort,
    KeysetFilterPort,
)


def _builtins():
    from culture_rules.node.runner import default_ports
    from culture_rules.store.memory import MemoryStore

    return default_ports(MemoryStore(), "spark")["code"]._builtins


def test_the_node_registers_keyset_filter():
    assert isinstance(_builtins()[KEYSET_FILTER_BUILTIN], KeysetFilterPort)


def test_the_node_registers_keyset_add():
    assert isinstance(_builtins()[KEYSET_ADD_BUILTIN], KeysetAddPort)


def test_keyset_filter_has_a_phrase():
    from culture_rules.model.describe import BUILTIN_WORDS

    assert BUILTIN_WORDS[KEYSET_FILTER_BUILTIN].startswith("keyset.filter:")


def test_keyset_add_has_a_phrase():
    from culture_rules.model.describe import BUILTIN_WORDS

    assert BUILTIN_WORDS[KEYSET_ADD_BUILTIN].startswith("keyset.add:")


def test_the_runner_docstring_names_keyset_filter():
    from culture_rules.node import runner

    assert "keyset.filter" in runner.__doc__


def test_the_runner_docstring_names_keyset_add():
    from culture_rules.node import runner

    assert "keyset.add" in runner.__doc__
