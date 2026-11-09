"""Existing LCD frame protocol; caller owns the shared display lock."""
import os

FRAME_BYTES = 320 * 240 * 3


class LCD:
    def __init__(self, path='/dev/m3100-lcd'):
        self.fd = os.open(path, os.O_WRONLY)

    def write_frame(self, frame):
        if len(frame) != FRAME_BYTES:
            raise ValueError('LCD requires exactly 230400 RGB888 bytes')
        if self.fd is None:
            raise OSError('LCD closed')
        if os.write(self.fd, b'frame\n') != 6:
            raise OSError('short LCD command write')
        if os.write(self.fd, frame) != FRAME_BYTES:
            raise OSError('short LCD frame write')

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
