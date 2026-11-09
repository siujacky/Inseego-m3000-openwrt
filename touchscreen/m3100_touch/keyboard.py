"""Paged printable-ASCII editor. Secrets only appear as masks in views."""
PAGE_SIZE = 12
GROUPS = [('lower', 'abc', 'abcdefghijklmnopqrstuvwxyz'),
          ('upper', 'ABC', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'),
          ('numeric', '123', '0123456789'),
          ('symbol', '#+=', ''.join(chr(i) for i in range(32, 127) if not chr(i).isalnum()))]
PAGES = [(mode, chars[i:i + PAGE_SIZE]) for mode, _, chars in GROUPS for i in range(0, len(chars), PAGE_SIZE)]


class Keyboard:
    def __init__(self, text='', secret=False, max_length=256):
        if not isinstance(max_length, int) or not 0 <= max_length <= 4096:
            raise ValueError('max_length must be between 0 and 4096')
        self.text = str(text)
        self.initial = self.text
        self.secret = bool(secret)
        self.max_length = max_length
        self.page = 0
        self.page_count = len(PAGES)

    def view(self):
        mode, chars = PAGES[self.page]
        group_pages = [i for i, (m, _) in enumerate(PAGES) if m == mode]
        return dict(kind='keyboard', title='Enter text', value='*' * len(self.text) if self.secret else self.text,
                    secret=self.secret, page=self.page, pages=self.page_count, mode=mode,
                    page_label='%d/%d' % (group_pages.index(self.page) + 1, len(group_pages)),
                    tabs=[dict(id='mode:' + m, label=label, selected=m == mode) for m, label, _ in GROUPS],
                    keys=[dict(id='char:' + c, label='Space' if c == ' ' else c) for c in chars],
                    footer=[dict(id=i, label=label) for i, label in [('prev', '<'), ('delete', 'Del'), ('next', '>'), ('cancel', 'Cancel'), ('done', 'Done')]])

    def press(self, id):
        if id == 'done':
            return 'done'
        if id == 'cancel':
            self.text = self.initial
            return 'cancel'
        if id in ('next', 'prev'):
            self.page = (self.page + (1 if id == 'next' else -1)) % self.page_count
        elif id.startswith('mode:'):
            mode = id[5:]
            self.page = next((i for i, (m, _) in enumerate(PAGES) if m == mode), self.page)
        elif id == 'delete':
            self.text = self.text[:-1]
        elif id == 'space':
            if len(self.text) < self.max_length:
                self.text += ' '
        elif id.startswith('char:'):
            c = id[5:]
            if len(c) == 1 and 32 <= ord(c) <= 126 and len(self.text) < self.max_length:
                self.text += c
