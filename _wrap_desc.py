import re
import sys

path = r'src\layout_canvas\mcp\server.py'
raw = open(path, 'rb').read()
text = raw.decode('utf-8')
lines = text.split('\n')
pat = re.compile(r'^(\s*)("description":\s*)"(.*)"(,?)\s*$')
out = []
skipped = []
changed = 0
for i, line in enumerate(lines, 1):
    if len(line) <= 100:
        out.append(line)
        continue
    m = pat.match(line)
    if not m:
        skipped.append((i, line))
        out.append(line)
        continue
    indent, key, s, comma = m.groups()
    if '"' in s or '\\' in s:
        skipped.append((i, line))
        out.append(line)
        continue
    qcol = len(indent) + len(key)
    budget = 97 - qcol - len(comma)
    words = s.split(' ')
    pieces = []
    cur = ''
    for w in words:
        cand = w if not cur else cur + ' ' + w
        if len(cand) <= budget:
            cur = cand
        else:
            if cur:
                pieces.append(cur + ' ')
            cur = w
    pieces.append(cur)
    assert ''.join(pieces) == s, f'piece join mismatch line {i}'
    new_lines = [indent + key + '"' + pieces[0] + '"']
    for p in pieces[1:-1]:
        new_lines.append(' ' * qcol + '"' + p + '"')
    if len(pieces) > 1:
        new_lines.append(' ' * qcol + '"' + pieces[-1] + '"' + comma)
    else:
        new_lines[0] += comma
    for nl in new_lines:
        assert len(nl) <= 100, f'overlong generated line {i}: {len(nl)}'
    out.extend(new_lines)
    changed += 1
new_text = '\n'.join(out)
open(path, 'wb').write(new_text.encode('utf-8'))
print(f'wrapped {changed} description lines; skipped {len(skipped)} overlong lines:')
for n, l in skipped:
    print(f'{n}: {l}')
