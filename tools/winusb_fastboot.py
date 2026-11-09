"""M3100 fastboot transport for its existing libwdi WinUSB interface.

Uses the observed device path; does not install drivers. No flash/erase/unlock
commands are exposed. This is an experimental local bring-up helper.
"""
import ctypes as C
from ctypes import wintypes as W
import json

PATH = r'\\?\USB#VID_18D1&PID_D00D#76fe1623#{6c483852-c580-4be7-b089-069c89d39d27}'


class Interface(C.Structure):
    _pack_ = 1
    _fields_ = [(n, C.c_ubyte) for n in ('length', 'type', 'number', 'alt',
                'endpoints', 'class_', 'subclass', 'protocol', 'string')]


class Pipe(C.Structure):
    _fields_ = [('type', C.c_int), ('id', C.c_ubyte),
                ('max_packet', W.USHORT), ('interval', C.c_ubyte)]


class Fastboot:
    def __init__(self):
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.w = C.WinDLL('winusb', use_last_error=True)
        self.k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                                      W.DWORD, W.DWORD, W.HANDLE]
        self.k.CreateFileW.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.w.WinUsb_Initialize.argtypes = [W.HANDLE, C.POINTER(W.HANDLE)]
        self.w.WinUsb_Free.argtypes = [W.HANDLE]
        self.w.WinUsb_QueryInterfaceSettings.argtypes = [W.HANDLE, C.c_ubyte, C.POINTER(Interface)]
        self.w.WinUsb_QueryPipe.argtypes = [W.HANDLE, C.c_ubyte, C.c_ubyte, C.POINTER(Pipe)]
        self.w.WinUsb_SetPipePolicy.argtypes = [W.HANDLE, C.c_ubyte, W.ULONG, W.ULONG, C.c_void_p]
        for name in ('WinUsb_ReadPipe', 'WinUsb_WritePipe'):
            getattr(self.w, name).argtypes = [W.HANDLE, C.c_ubyte, C.c_void_p,
                                             W.ULONG, C.POINTER(W.ULONG), C.c_void_p]
        self.file = self.k.CreateFileW(PATH, 0xc0000000, 3, None, 3, 0x40000000, None)
        if self.file == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        self.usb = W.HANDLE()
        self.ok(self.w.WinUsb_Initialize(self.file, C.byref(self.usb)))
        interface = Interface()
        self.ok(self.w.WinUsb_QueryInterfaceSettings(self.usb, 0, C.byref(interface)))
        if (interface.class_, interface.subclass, interface.protocol) != (255, 66, 3):
            self.close()
            raise RuntimeError('Interface is not Android fastboot')
        self.endpoints = {}
        for i in range(interface.endpoints):
            pipe = Pipe()
            self.ok(self.w.WinUsb_QueryPipe(self.usb, 0, i, C.byref(pipe)))
            if pipe.type == 2:
                self.endpoints['in' if pipe.id & 128 else 'out'] = pipe.id
                timeout = W.ULONG(10000)
                self.ok(self.w.WinUsb_SetPipePolicy(self.usb, pipe.id, 3, C.sizeof(timeout), C.byref(timeout)))
        if set(self.endpoints) != {'in', 'out'}:
            raise RuntimeError('Expected one bulk IN and one bulk OUT endpoint')

    @staticmethod
    def ok(result):
        if not result:
            raise C.WinError(C.get_last_error())

    def write(self, data):
        buf = C.create_string_buffer(data)
        count = W.ULONG()
        self.ok(self.w.WinUsb_WritePipe(self.usb, self.endpoints['out'], buf,
                                       len(data), C.byref(count), None))
        if count.value != len(data):
            raise RuntimeError('Short fastboot write')

    def read(self):
        buf = C.create_string_buffer(4096)
        count = W.ULONG()
        self.ok(self.w.WinUsb_ReadPipe(self.usb, self.endpoints['in'], buf,
                                      len(buf), C.byref(count), None))
        return buf.raw[:count.value]

    def response(self):
        info = []
        for _ in range(100):
            reply = self.read()
            tag, text = reply[:4], reply[4:].rstrip(b'\0').decode('ascii', errors='replace')
            if tag == b'INFO':
                info.append(text)
            elif tag in (b'OKAY', b'FAIL', b'DATA'):
                return {'status': tag.decode(), 'value': text, 'info': info}
            else:
                raise RuntimeError('Unexpected fastboot response: ' + repr(reply))
        raise RuntimeError('Too many fastboot INFO responses')

    def command(self, command):
        if not (command.startswith('getvar:') or command in ('reboot', 'boot')):
            raise ValueError('Command not allowed by RAM-only helper')
        self.write(command.encode('ascii'))
        return self.response()

    def download_ram_image(self, data):
        from check_ram_image import check_boot_image
        check_boot_image(data)
        self.write(('download:%08x' % len(data)).encode('ascii'))
        reply = self.response()
        if reply['status'] != 'DATA' or int(reply['value'], 16) != len(data):
            raise RuntimeError('Download refused or size mismatch: ' + repr(reply))
        for offset in range(0, len(data), 65536):
            self.write(data[offset:offset + 65536])
        reply = self.response()
        if reply['status'] != 'OKAY':
            raise RuntimeError('Download failed: ' + repr(reply))
        return reply

    def close(self):
        if getattr(self, 'usb', None):
            self.w.WinUsb_Free(self.usb)
        if getattr(self, 'file', None):
            self.k.CloseHandle(self.file)


if __name__ == '__main__':
    connection = Fastboot()
    try:
        print(json.dumps({k: connection.command('getvar:' + k) for k in
              ('product', 'serialno', 'unlocked', 'secure', 'max-download-size')}, indent=2))
    finally:
        connection.close()
