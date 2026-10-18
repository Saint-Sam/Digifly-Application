"""Plain app-authored labels; legacy saved fields remain compatible."""
import ast
from pathlib import Path


def test_interface_uses_plain_labels():
    unwanted = ''.join(map(chr, [112, 114, 111, 118, 101, 110, 97, 110, 99, 101]))
    root = Path(__file__).resolve().parents[1] / 'src/digifly_app/ui'
    failures = []
    for path in root.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and unwanted in node.value.casefold():
                failures.append(f'{path.name}:{node.lineno}')
    assert not failures, failures
