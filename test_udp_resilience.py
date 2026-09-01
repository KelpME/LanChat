"""Verify the UDP listener survives a handler exception (the .51 bug:
a missing cryptography dep killed the listener thread silently)."""
import sys, os, json, tempfile, subprocess, time, threading, shutil, socket
sys.path.insert(0,'.')
# Simulate: run a daemon, then send a malformed packet AND one that triggers
# an exception in a handler; the listener must survive and still process hello.
def make_home(name, port):
    d = tempfile.mkdtemp(prefix='resil-'+name)
    cfg = os.path.join(d,'.config','omarchy'); os.makedirs(cfg, exist_ok=True)
    open(os.path.join(cfg,'lanchat.json'),'w').write(json.dumps({'token':'x'*16,'port':port,'displayName':name}))
    return d
class D:
    def __init__(self, home, port):
        self.port=port; self.events=[]; self._lock=threading.Lock()
        self.proc=subprocess.Popen([sys.executable,'server.py'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=dict(os.environ,HOME=home),bufsize=1,text=True)
        threading.Thread(target=self._read,daemon=True).start()
    def _read(self):
        for line in self.proc.stdout:
            try: ev=json.loads(line)
            except: continue
            with self._lock: self.events.append(ev)
    def cmd(self,**kw): self.proc.stdin.write(json.dumps(kw)+'\n'); self.proc.stdin.flush()
    def wait_event(self,kind,timeout=4):
        end=time.time()+timeout
        while time.time()<end:
            with self._lock:
                for ev in self.events:
                    if ev.get('event')==kind: self.events.remove(ev); return ev
            time.sleep(0.02)
    def stop(self):
        try: self.proc.stdin.close()
        except: pass
        try: self.proc.wait(timeout=5)
        except: self.proc.kill()
ha=make_home('A',4981); hb=make_home('B',4982)
a=D(ha,4981); b=D(hb,4982)
time.sleep(1.5)
# 1. malformed packet (bad JSON) must not kill listener
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.sendto(b'NOT JSON {{{', ('127.0.0.1',4982)); s.close()
# 2. a packet that raises in handler: friend-request with huge/malformed fields
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.sendto(json.dumps({'t':'friend-request','id':'x'*64,'cert':'','nonce':'','sig':''}).encode(),('127.0.0.1',4982)); s.close()
time.sleep(0.8)
# 3. now a valid hello must STILL be processed (listener alive)
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.sendto(json.dumps({'t':'hello','id':'probe-after-bad','name':'P','port':4981}).encode(),('127.0.0.1',4982)); s.close()
peered = None
end=time.time()+3
while time.time()<end:
    with b._lock:
        for ev in b.events:
            if ev.get('event')=='peer' and ev['peer'].get('id')=='probe-after-bad':
                peered=True
    if peered: break
    time.sleep(0.1)
assert peered, "UDP listener died after a bad packet — the .51 bug"
print("OK  UDP listener survives bad packets and keeps discovering (fixed)")
a.stop(); b.stop()
shutil.rmtree(ha,ignore_errors=True); shutil.rmtree(hb,ignore_errors=True)
