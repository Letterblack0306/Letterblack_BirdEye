from __future__ import annotations
import argparse, base64, ctypes, io, json, os, sys, hashlib
from PIL import ImageGrab
from ctypes import wintypes

def bounds():
    u=ctypes.windll.user32
    return {'left':u.GetSystemMetrics(76),'top':u.GetSystemMetrics(77),'width':u.GetSystemMetrics(78),'height':u.GetSystemMetrics(79)}

def action(args):
    u=ctypes.windll.user32
    if os.name!='nt': raise RuntimeError('Windows desktop required')
    b=bounds()
    if args.action=='capture':
        img=ImageGrab.grab(all_screens=True)
        if args.max_width and img.width>args.max_width:
            img.thumbnail((args.max_width, int(img.height*args.max_width/img.width)))
        data=io.BytesIO()
        img.save(data,format='PNG',optimize=True)
        raw=data.getvalue()
        if args.output:
            with open(args.output,'xb') as f: f.write(raw)
            value={'ok':True,'action':'capture','bounds':b,'image_width':img.width,'image_height':img.height,'mimeType':'image/png','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        else:
            value={'ok':True,'action':'capture','bounds':b,'image_width':img.width,'image_height':img.height,'mimeType':'image/png','imageBase64':base64.b64encode(raw).decode('ascii')}
        print(json.dumps(value,separators=(',',':')))
        return
    if args.action=='cursor':
        p=wintypes.POINT()
        if not u.GetCursorPos(ctypes.byref(p)): raise RuntimeError('GetCursorPos failed')
        print(json.dumps({'ok':True,'action':'cursor','x':p.x,'y':p.y,'bounds':b}))
        return
    if args.action in ('move','click'):
        if args.x is None or args.y is None: raise ValueError('x and y required')
        if not (b['left']<=args.x<b['left']+b['width'] and b['top']<=args.y<b['top']+b['height']): raise ValueError('coordinates outside desktop')
        if not u.SetCursorPos(args.x,args.y): raise RuntimeError('SetCursorPos failed')
        if args.action=='click':
            flags={'left':(2,4),'right':(8,16),'middle':(32,64)}
            down,up=flags[args.button]
            u.mouse_event(down,0,0,0,0)
            u.mouse_event(up,0,0,0,0)
        print(json.dumps({'ok':True,'action':args.action,'x':args.x,'y':args.y,'button':args.button}))
        return
    if args.action=='type':
        if not args.text or len(args.text)>1000: raise ValueError('text length must be 1-1000')
        # SendInput with KEYEVENTF_UNICODE, one UTF-16 unit at a time.
        ULONG_PTR=ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p)==8 else ctypes.c_ulong
        class KEYBDINPUT(ctypes.Structure):
            _fields_=[('wVk',wintypes.WORD),('wScan',wintypes.WORD),('dwFlags',wintypes.DWORD),('time',wintypes.DWORD),('dwExtraInfo',ULONG_PTR)]
        class MOUSEINPUT(ctypes.Structure):
            _fields_=[('dx',wintypes.LONG),('dy',wintypes.LONG),('mouseData',wintypes.DWORD),('dwFlags',wintypes.DWORD),('time',wintypes.DWORD),('dwExtraInfo',ULONG_PTR)]
        class HARDWAREINPUT(ctypes.Structure):
            _fields_=[('uMsg',wintypes.DWORD),('wParamL',wintypes.WORD),('wParamH',wintypes.WORD)]
        class INPUT_UNION(ctypes.Union):
            _fields_=[('mi',MOUSEINPUT),('ki',KEYBDINPUT),('hi',HARDWAREINPUT)]
        class INPUT(ctypes.Structure):
            _fields_=[('type',wintypes.DWORD),('u',INPUT_UNION)]
        units=args.text.encode('utf-16-le')
        for i in range(0,len(units),2):
            scan=int.from_bytes(units[i:i+2],'little')
            for flag in (4,6):
                packet=INPUT(type=1,u=INPUT_UNION(ki=KEYBDINPUT(0,scan,flag,0,0)))
                if u.SendInput(1,ctypes.byref(packet),ctypes.sizeof(INPUT))!=1: raise RuntimeError('SendInput failed')
        print(json.dumps({'ok':True,'action':'type','codepoints':len(args.text)}))
        return
    raise ValueError('unsupported action')

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('action',choices=['capture','cursor','move','click','type'])
    ap.add_argument('--x',type=int)
    ap.add_argument('--y',type=int)
    ap.add_argument('--button',choices=['left','right','middle'],default='left')
    ap.add_argument('--text')
    ap.add_argument('--max-width',type=int,default=1280)
    ap.add_argument('--output')
    a=ap.parse_args()
    try: action(a)
    except Exception as e:
        print(json.dumps({'ok':False,'error':type(e).__name__,'message':str(e)}),file=sys.stderr)
        sys.exit(1)
