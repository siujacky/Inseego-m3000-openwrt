"""Pure 320x240 RGB888 rendering, bounded controls and case-preserving text.

Controller owns pagination: ordinary rows <=3, home tiles <=6. Excess rows are
never given hidden hitboxes. Footer is a list of {id,label,enabled}; text footer
is shown as status. Rendering never executes actions or mutates the view.
"""
from .font import glyph, WIDTH, HEIGHT

W, H = 320, 240
ROW_CAPACITY = 3
HOME_CAPACITY = 6
BG = (13, 23, 34)
PANEL = (34, 49, 65)
FG = (237, 244, 249)
MUTED = (155, 176, 194)
ACCENT = (26, 110, 161)
DISABLED = (53, 62, 70)


def wrap_text(value, columns, max_lines=2):
    """Wrap words and long tokens, mark any omitted continuation with '...'."""
    columns = max(1, int(columns))
    max_lines = max(1, int(max_lines))
    text = str(value).replace('\t', ' ').replace('\r', '')[:16384]
    lines = []
    for paragraph in text.split('\n'):
        remaining = paragraph
        while len(remaining) > columns:
            split = remaining.rfind(' ', 0, columns + 1)
            split = split if split > 0 else columns
            lines.append(remaining[:split])
            remaining = remaining[split:].lstrip()
        lines.append(remaining)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        dots = '.' * min(3, columns)
        lines[-1] = lines[-1][:columns - len(dots)] + dots
    return lines


class Canvas:
    def __init__(self, background=BG, foreground=FG):
        self.pixels = bytearray(bytes(background) * (W * H))
        self.foreground = foreground

    def rect(self, x, y, w, h, color):
        x0, x1 = max(0, x), min(W, x + w)
        y0, y1 = max(0, y), min(H, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for yy in range(y0, y1):
            start = (yy * W + x0) * 3
            self.pixels[start:start + len(row)] = row

    def text(self, value, x, y, color=None, columns=34, lines=1):
        color = self.foreground if color is None else color
        for iy, line in enumerate(wrap_text(value, columns, lines)):
            for ix, char in enumerate(line):
                bitmap = glyph(char)
                for row in range(HEIGHT):
                    bits = bitmap[row * 2] | bitmap[row * 2 + 1] << 8
                    for col in range(WIDTH):
                        xx, yy = x + ix * WIDTH + col, y + iy * HEIGHT + row
                        if bits & (1 << col) and 0 <= xx < W and 0 <= yy < H:
                            p = (yy * W + xx) * 3
                            self.pixels[p:p + 3] = bytes(color)


def render(view):
    if view.get('theme') == 'light':
        bg, fg, muted = (240, 245, 249), (15, 33, 48), (68, 90, 108)
        panel, disabled, header, accent = (216, 229, 239), (221, 224, 227), (195, 216, 232), (111, 185, 225)
    else:
        bg, fg, muted = BG, FG, MUTED
        panel, disabled, header, accent = PANEL, DISABLED, (21, 39, 54), ACCENT
    canvas = Canvas(bg, fg)
    boxes = []
    pressed = view.get('pressed')
    kind = view.get('kind', 'list')
    canvas.rect(0, 0, W, 34, header)
    canvas.text(view.get('title', '5G Router'), 7, 8, columns=27 if kind == 'keyboard' else 34)
    if kind == 'keyboard':
        canvas.text(view.get('page_label', ''), 270, 8, muted, columns=5)

    def button(row, x, y, w, h, label=None, value=None, padding=5):
        enabled = bool(row.get('enabled', True))
        identity = row.get('id')
        interactive = identity is not None and row.get('kind') != 'text'
        color = accent if enabled and interactive and (identity == pressed or row.get('selected')) else panel if enabled else disabled
        canvas.rect(x, y, w, h, color)
        if interactive:
            boxes.append(dict(id=identity, x=x, y=y, w=w, h=h, enabled=enabled))
        label = row.get('label', '') if label is None else label
        if value is not None and str(value) and len(str(label)) <= max(1, (w - 2 * padding) // WIDTH) and '\n' not in str(label):
            canvas.text(label, x + padding, y + 2, fg if enabled else muted, columns=max(1, (w - 2 * padding) // WIDTH))
            canvas.text(value, x + padding, y + 22, muted, columns=max(1, (w - 2 * padding) // WIDTH))
        else:
            canvas.text(label, x + padding, y + (4 if h < 52 else 10), fg if enabled else muted,
                        columns=max(1, (w - 2 * padding) // WIDTH), lines=min(2, (h - 8) // HEIGHT))

    rows = view.get('rows') or []
    if kind == 'keyboard':
        # Secret keyboard views already contain a mask; mask again at the boundary.
        value = str(view.get('value', ''))
        if view.get('secret'):
            value = '*' * len(value)
        canvas.text(value[-33:], 7, 30, columns=33)
        for i, key in enumerate((view.get('keys') or [])[:12]):
            button(key, 4 + (i % 6) * 53, 52 + (i // 6) * 47, 48, 42, padding=1)
        tabs = (view.get('tabs') or [])[:4]
        if tabs:
            for i, tab in enumerate(tabs):
                button(tab, i * 80 + 1, 148, 78, 40)
        else:
            canvas.text('Page %s/%s' % (view.get('page', 0) + 1, view.get('pages', 1)), 7, 150, muted)
    elif kind == 'home':
        for i, row in enumerate(rows[:HOME_CAPACITY]):
            button(row, 4 + (i % 3) * 106, 40 + (i // 3) * 68, 100, 62)
    elif rows:
        for i, row in enumerate(rows[:ROW_CAPACITY]):
            value = row.get('value', '')
            if row.get('secret'):
                value = '*' * min(32, len(str(value)))
            button(row, 4, 40 + i * 47, 312, 44, value=value)
    else:
        message = view.get('message', '')
        if message:
            canvas.text(message, 7, 42, columns=34, lines=8)

    footer = view.get('footer') or []
    status = view.get('status', '')
    if isinstance(footer, str):
        status = status or footer
        footer = []
    if status and kind != 'keyboard':
        canvas.text(status, 7, 178, muted, columns=34)
    footer = footer[:5]
    if footer:
        count = len(footer)
        for i, item in enumerate(footer):
            x0, x1 = i * W // count, (i + 1) * W // count
            button(item, x0 + 1, 196, x1 - x0 - 2, 44, padding=3)
    return bytes(canvas.pixels), boxes
