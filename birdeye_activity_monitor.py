import json, tkinter as tk
from pathlib import Path
from datetime import datetime, timezone
JOURNAL=Path(__file__).resolve().parent/'state'/'workspace_journal.jsonl'
w=tk.Tk(); w.title('BirdEye Activity Monitor'); w.geometry('390x175-20+40'); w.overrideredirect(True); w.attributes('-topmost',True); w.attributes('-alpha',0.62); w.configure(bg='#161b22')
def label(t,size,color): 
 x=tk.Label(w,text=t,font=('Segoe UI',size),fg=color,bg='#161b22',justify='left',wraplength=360);x.pack(anchor='w',padx=14,pady=6);return x
label('BirdEye | Execution receipts',14,'white')
status=label('INITIALIZING',16,'#f0c674');detail=label('',10,'#c9d1d9')
label('Receipt-based only. ACTIVE execution is not yet instrumented.\nNo receipt does not prove work is running.',9,'#8b949e')
def update():
 try:
  with JOURNAL.open('rb') as f:
   f.seek(max(0,JOURNAL.stat().st_size-131072));lines=f.read().decode('utf-8',errors='replace').splitlines()
  rows=[]
  for line in lines[-25:]:
   try: rows.append(json.loads(line))
   except ValueError: pass
  item=rows[-1]; stamp=datetime.fromisoformat(item['timestamp'].replace('Z','+00:00'));age=max(0,int((datetime.now(timezone.utc)-stamp).total_seconds()))
  status.config(text='RECENT COMPLETION' if age<15 else 'IDLE / NO RECENT RECEIPT',fg='#58a6ff' if age<15 else '#c9d1d9')
  detail.config(text='Result: '+str(item.get('classification'))+' | Exit: '+str(item.get('exit_code'))+'\nWorkspace: '+str(item.get('workspace'))[:55]+'\nLast receipt: '+stamp.astimezone().strftime('%H:%M:%S')+' ('+str(age)+' sec ago)')
 except Exception as e: status.config(text='NO RECEIPTS');detail.config(text=str(e))
 w.after(1000,update)
import ctypes
from ctypes import wintypes

def enable_click_through():
    user32=ctypes.windll.user32
    hwnd=w.winfo_id(); hwnd=user32.GetAncestor(hwnd,2) or hwnd
    GWL_EXSTYLE=-20
    WS_EX_TRANSPARENT=0x20
    WS_EX_LAYERED=0x80000
    style=user32.GetWindowLongW(hwnd,GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd,GWL_EXSTYLE,style|WS_EX_TRANSPARENT|WS_EX_LAYERED)
    user32.SetWindowPos(hwnd,0,0,0,0,0,0x0001|0x0002|0x0004|0x0020)

w.after(400,enable_click_through)
update();w.mainloop()

