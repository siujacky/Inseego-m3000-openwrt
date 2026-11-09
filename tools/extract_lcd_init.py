"""Recover the normal-path ST7789 init literals from saved ARM disassembly.

This is deliberately a limited static evaluator, not a hardware emulator.
Branches/delay loops are not executed. Unknown loads are marked unknown.
"""
from pathlib import Path
import json
import re

root = Path(__file__).resolve().parents[1]
source = root / 'stock/drivers/built-in-analysis/lcd/disassembly.txt'
text = source.read_text().split('<st7789_init_v3a>:', 1)[1].split('<st7789_suspend>:', 1)[0]
regs = {'sp': 0x10000}
memory = {}
commands = []

def value(s):
    return int(s[1:], 0) if s.startswith('#') else regs.get(s)

for line in text.splitlines():
    m = re.match(r'([0-9a-f]+): [0-9a-f]+\s+(\w+)\s+(.*)', line)
    if not m:
        continue
    pc, op, operands = m.groups()
    if int(pc, 16) >= 0xb04bccc4:
        break  # Normal path ends here; remaining code is debug/stack checking.
    args = operands.split(' ;')[0].split(', ')
    if op in ('mov', 'movw', 'mvn'):
        v = value(args[1])
        regs[args[0]] = None if v is None else ((~v if op == 'mvn' else v) & 0xffffffff)
    elif op == 'movt':
        old, v = regs.get(args[0]), value(args[1])
        regs[args[0]] = None if old is None or v is None else ((old & 0xffff) | (v << 16))
    elif op in ('add', 'sub'):
        a, b = value(args[1]), value(args[2])
        regs[args[0]] = None if a is None or b is None else (a + b if op == 'add' else a - b)
    elif op in ('str', 'strb', 'strh'):
        address = re.search(r'\[(\w+)(?:, #([^\]]+))?\]', operands)
        if address:
            base = regs.get(address[1])
            v = value(args[0])
            if base is not None and v is not None:
                address = base + int(address[2] or '0', 0)
                for i in range({'str': 4, 'strh': 2, 'strb': 1}[op]):
                    memory[address + i] = (v >> (8 * i)) & 255
    elif op in ('ldr', 'ldrb'):
        regs[args[0]] = None
    elif op == 'bl' and 'st7789_write_reg' in operands:
        command, address, count = (regs.get(r) for r in ('r1', 'r2', 'r3'))
        assert command is not None and count is not None
        data = [memory.get(address + i) for i in range(count)]
        assert all(v is not None for v in data), (pc, data)
        commands.append({'pc': '0x' + pc, 'command': command, 'data': data})
        regs['r0'] = 0
    elif op == 'bl' and 'st7789_orientation' in operands:
        orientation = regs.get('r1')
        # Verified separately in st7789_orientation: argument 0 -> 0x60,
        # argument 1 -> 0xa0, both sent as command 0x36 with one byte.
        assert orientation in (0, 1)
        commands.append({'pc': '0x' + pc, 'orientation_argument': orientation,
                         'command': 0x36, 'data': [0x60 if orientation == 0 else 0xa0],
                         'callee_command_pc': '0xb04bc91c'})

assert len(commands) >= 15 and commands[0]['command'] == 0x11
output = root / 'analysis/lcd-stock-init-literals.json'
output.write_text(json.dumps({'source': str(source.relative_to(root)),
    'method': 'Static normal-path literal recovery; delay durations pending',
    'commands': commands}, indent=2) + '\n')
print(output)
print(json.dumps(commands))
