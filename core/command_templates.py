"""Explicit template fields; ordinary shell braces remain untouched."""
import re

FIELD = re.compile(r'(?<!\{)\{([A-Za-z_\u3400-\u9fff][\w\u3400-\u9fff-]*)(?::(file|dir|text))?\}(?!\})')


def fields(command):
    return list(dict.fromkeys((m.group(1), m.group(2) or 'text') for m in FIELD.finditer(command)))


def render(command, values):
    def replace(match):
        name = match.group(1)
        value = values.get(name, '').strip()
        if not value:
            raise ValueError(f'请填写 {name}')
        return value
    return FIELD.sub(replace, command)
