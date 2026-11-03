"""text.number, text.replace_refs and text.validate are registered on the node."""

import pytest

from culture_rules.node.actions.text_refs import TEXT_REPLACE_REFS_BUILTIN, TextReplaceRefsPort
from culture_rules.node.actions.text_steps import TEXT_NUMBER_BUILTIN, TextNumberPort
from culture_rules.node.actions.text_validate import TEXT_VALIDATE_BUILTIN, TextValidatePort

CASES = [
    (TEXT_NUMBER_BUILTIN, TextNumberPort),
    (TEXT_REPLACE_REFS_BUILTIN, TextReplaceRefsPort),
    (TEXT_VALIDATE_BUILTIN, TextValidatePort),
]


def _builtins():
    from culture_rules.node.runner import default_ports
    from culture_rules.store.memory import MemoryStore

    return default_ports(MemoryStore(), "spark")["code"]._builtins


@pytest.mark.parametrize(("name", "port"), CASES)
def test_the_node_registers_the_step(name, port):
    assert isinstance(_builtins()[name], port)
