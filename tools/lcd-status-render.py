"""Render a 320x240 RGB888 LCD status page using only Python stdlib.

Output is exactly 230400 bytes; optional PPM preview uses the same pixels.
Values are explicit labels, never inferred hardware telemetry.
"""
import argparse
from pathlib import Path

FONT = {
 'A':'010101111101101','B':'110101110101110','C':'011100100100011',
 'D':'110101101101110','E':'111100110100111','F':'111100110100100',
 'G':'011100101101011','H':'101101111101101','I':'111010010010111',
 'J':'001001001101010','K':'101101110101101','L':'100100100100111',
 'M':'101111111101101','N':'101111111111101','O':'010101101101010',
 'P':'110101110100100','Q':'010101101111011','R':'110101110101101',
 'S':'011100010001110','T':'111010010010010','U':'101101101101111',
 'V':'101101101101010','W':'101101111111101','X':'101101010101101',
 'Y':'101101010010010','Z':'111001010100111',
 '0':'111101101101111','1':'010110010010111','2':'110001010100111',
 '3':'110001010001110','4':'101101111001001','5':'111100110001110',
 '6':'011100111101111','7':'111001010010010','8':'111101111101111',
 '9':'111101111001110',' ':'000000000000000','.':'000000000000010',
 ':':'000010000010000','-':'000000111000000','/':'001001010100100',
 '?':'110001010000010',
}

def render(usb, battery, status):
    buf = bytearray(bytes((10, 18, 30)) * (320 * 240))
    def rect(x, y, w, h, color):
        assert x >= 0 and y >= 0 and x+w <= 320 and y+h <= 240
        for row in range(y, y+h):
            start = (row*320+x)*3
            buf[start:start+w*3] = bytes(color)*w
    def text(x, y, value, scale=3, color=(225,235,245)):
        for ch in value.upper():
            if x + 3*scale > 314:
                break
            glyph = FONT.get(ch, FONT['?'])
            for row in range(5):
                for col in range(3):
                    if glyph[row*3+col] == '1':
                        rect(x+col*scale,y+row*scale,scale,scale,color)
            x += 4*scale
    rect(0,0,320,6,(30,195,160))
    text(14,20,'OPENWRT',6,(30,220,175))
    text(14,62,'M3100 RAM SYSTEM',3)
    rect(14,90,292,1,(45,65,85))
    text(14,107,'USB: '+usb,3)
    text(14,143,'BATTERY: '+battery,3)
    text(14,183,status,3,(110,185,250))
    text(14,219,'HOST STATUS VALUES',2,(120,140,160))
    return bytes(buf)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--usb',default='UNKNOWN')
    ap.add_argument('--battery-v',type=float)
    ap.add_argument('--status',default='RAM BOOT ACTIVE')
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--ppm',type=Path)
    args=ap.parse_args()
    battery='UNKNOWN' if args.battery_v is None else f'{args.battery_v:.3f} V'
    data=render(args.usb,battery,args.status)
    assert len(data)==320*240*3
    args.output.write_bytes(data)
    if args.ppm:
        args.ppm.write_bytes(b'P6\n320 240\n255\n'+data)
    print(f'{args.output}: {len(data)} RGB888 bytes')

if __name__=='__main__':
    main()
