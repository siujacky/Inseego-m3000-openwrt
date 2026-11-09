"""Strict newc parser/writer for isolated LAN147 archive derivation."""
from pathlib import Path
import hashlib

def digest(data):
    return hashlib.sha256(data).hexdigest()

def parse(blob):
    entries=[];offset=0
    while True:
        assert blob[offset:offset+6] in (b'070701',b'070702'), ('magic',offset)
        fields=[int(blob[offset+6+i*8:offset+14+i*8],16) for i in range(13)]
        size,namesize=fields[6],fields[11]
        assert namesize>0 and offset+110+namesize<=len(blob)
        rawname=blob[offset+110:offset+110+namesize]
        assert rawname[-1:]==b'\0' and b'\0' not in rawname[:-1]
        name=rawname[:-1].decode().removeprefix('./')
        payload=(offset+110+namesize+3)&~3;end=(payload+size+3)&~3
        assert end<=len(blob)
        data=blob[payload:payload+size]
        if blob[offset:offset+6]==b'070702':assert sum(data)&0xffffffff==fields[12]
        entries.append({'name':name,'fields':fields,'raw':blob[offset:end],'payload':data})
        offset=end
        if name=='TRAILER!!!':
            assert not any(blob[offset:]),'nonzero trailing data'
            assert len({e['name'] for e in entries})==len(entries),'duplicate names'
            return entries

def regular(name,payload,inode):
    assert name and not name.startswith('/') and '..' not in name.split('/')
    rawname=name.encode()+b'\0'
    fields=[inode,0o100644,0,0,1,0,len(payload),0,0,0,0,len(rawname),0]
    raw=b'070701'+b''.join(f'{v:08x}'.encode() for v in fields)+rawname
    raw+=b'\0'*((-len(raw))%4);raw+=payload;raw+=b'\0'*((-len(raw))%4)
    return raw
