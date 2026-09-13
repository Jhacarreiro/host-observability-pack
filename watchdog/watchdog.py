#!/usr/bin/env python3
import fcntl, glob, json, os, shutil, subprocess, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

def ef(name,default):
    try:return float(os.getenv(name,default))
    except ValueError:return float(default)
def ei(name,default):
    try:return int(os.getenv(name,default))
    except ValueError:return int(default)

MODE=os.getenv("WATCHDOG_MODE","observe").lower()
if MODE!="observe":raise SystemExit("v0.1 supports WATCHDOG_MODE=observe only")
RUNTIME=Path(os.getenv("WATCHDOG_RUNTIME_DIR","/var/lib/host-observability-pack/watchdog")); EVENTS=RUNTIME/"events"
STATE=RUNTIME/"state.json"; CURRENT=RUNTIME/"current.json"; LOCKFILE=RUNTIME/"watchdog.lock"
DOCKER=os.getenv("WATCHDOG_DOCKER_BIN","").strip() or shutil.which("docker") or "/usr/bin/docker"
LOAD_WARN=ef("WATCHDOG_LOAD_WARN",8); LOAD_HARD=ef("WATCHDOG_LOAD_HARD",8); D_WARN=ei("WATCHDOG_DSTATE_WARN",2); D_HARD=ei("WATCHDOG_DSTATE_HARD",3)
SWAP_WARN=ef("WATCHDOG_SWAP_WARN_RATIO",.90); DOCKER_WARN=ef("WATCHDOG_DOCKER_WARN_SECONDS",2); DOCKER_TIMEOUT=ef("WATCHDOG_DOCKER_TIMEOUT_SECONDS",5)
BTRFS_WARN=ef("WATCHDOG_BTRFS_COMMIT_WARN_SECONDS",10); STREAK=ei("WATCHDOG_RECOVERY_STREAK",5); REPEAT=ei("WATCHDOG_SNAPSHOT_REPEAT_SECONDS",1800)
RUNTIME.mkdir(parents=True,exist_ok=True); EVENTS.mkdir(parents=True,exist_ok=True)

def atomic(path,text,mode=0o640):
    fd,tmp=tempfile.mkstemp(prefix=path.name+".",dir=str(path.parent))
    try:
        with os.fdopen(fd,"w") as f:f.write(text); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,mode); os.replace(tmp,path)
    finally:
        try:os.unlink(tmp)
        except FileNotFoundError:pass

def load_json(path,default):
    try:return json.loads(path.read_text())
    except Exception:return default

def proc_states():
    counts={}; flagged=[]
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():continue
        try:
            raw=(p/"stat").read_text(errors="replace"); l,r=raw.find("("),raw.rfind(")")
            if l<0 or r<0 or r+2>=len(raw):continue
            st=raw[r+2:r+3]; comm=raw[l+1:r]; counts[st]=counts.get(st,0)+1
            if st in {"D","Z"}:
                try:wchan=(p/"wchan").read_text(errors="replace").strip()
                except Exception:wchan="?"
                flagged.append({"pid":int(p.name),"state":st,"comm":comm,"wchan":wchan})
        except Exception:pass
    return counts,flagged[:200]

def swap_ratio():
    vals={}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if ":" not in line:continue
        k,v=line.split(":",1)
        try:vals[k]=int(v.strip().split()[0])*1024
        except Exception:pass
    t,f=vals.get("SwapTotal",0),vals.get("SwapFree",0); return 0 if t<=0 else max(0,min(1,(t-f)/t))

def btrfs():
    last=maxc=0
    for p in glob.glob("/sys/fs/btrfs/*/commit_stats"):
        try:
            for line in Path(p).read_text().splitlines():
                x=line.split()
                if len(x)!=2:continue
                if x[0]=="last_commit_ms":last=max(last,int(x[1]))
                if x[0]=="max_commit_ms":maxc=max(maxc,int(x[1]))
        except Exception:pass
    return last/1000,maxc/1000

def docker_probe():
    start=time.monotonic()
    try:
        p=subprocess.run([DOCKER,"info"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=DOCKER_TIMEOUT,check=False)
        return p.returncode==0,time.monotonic()-start,p.returncode
    except subprocess.TimeoutExpired:return False,time.monotonic()-start,124
    except Exception:return False,time.monotonic()-start,127

def bounded(path,limit=16000):
    try:return Path(path).read_text(errors="replace")[-limit:]
    except Exception as e:return f"{type(e).__name__}: {e}"
def docker_ps():
    try:
        p=subprocess.run([DOCKER,"ps","--format","{{.Names}}|{{.Status}}|{{.Image}}"],capture_output=True,text=True,timeout=DOCKER_TIMEOUT,check=False)
        return p.stdout[-16000:] if p.returncode==0 else f"rc={p.returncode}: {p.stderr[-2000:]}"
    except Exception as e:return f"{type(e).__name__}: {e}"
def snap(now_iso,metrics,flagged,reason):
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    obj={"timestamp":now_iso,"mode":MODE,"reason":reason,"metrics":metrics,"blocked_or_zombie":flagged,"proc_loadavg":bounded("/proc/loadavg"),
         "proc_meminfo":bounded("/proc/meminfo"),"proc_vmstat":bounded("/proc/vmstat"),"proc_diskstats":bounded("/proc/diskstats"),"proc_mdstat":bounded("/proc/mdstat"),
         "docker_ps":docker_ps() if metrics["docker_up"] else "docker unavailable"}
    path=EVENTS/f"{stamp}-{reason}.json"; atomic(path,json.dumps(obj,indent=2,sort_keys=True)+"\n"); return str(path)

def main():
    lock=LOCKFILE.open("w")
    try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return 0
    now=time.time(); now_iso=datetime.now(timezone.utc).isoformat(); load1=float(Path("/proc/loadavg").read_text().split()[0])
    counts,flagged=proc_states(); d=counts.get("D",0); z=counts.get("Z",0); swap=swap_ratio(); blast,bmax=btrfs(); dup,dlat,drc=docker_probe()
    state=load_json(STATE,{"critical_streak":0,"docker_fail_streak":0,"would_recover":False,"last_snapshot_at":0})
    state["docker_fail_streak"]=0 if dup else int(state.get("docker_fail_streak",0))+1
    candidate=(d>=D_HARD and load1>=LOAD_HARD) or (blast>=BTRFS_WARN and d>=D_WARN) or ((not dup) and d>=D_WARN and load1>=LOAD_HARD)
    state["critical_streak"]=int(state.get("critical_streak",0))+1 if candidate else 0; would=state["critical_streak"]>=STREAK
    warning=d>=D_WARN or load1>=LOAD_WARN or swap>=SWAP_WARN or not dup or dlat>=DOCKER_WARN or blast>=BTRFS_WARN
    m={"timestamp":now_iso,"mode":MODE,"load1":load1,"dstate":d,"zombies":z,"swap_used_ratio":round(swap,6),"docker_up":dup,"docker_latency_seconds":round(dlat,4),
       "docker_rc":drc,"docker_fail_streak":state["docker_fail_streak"],"btrfs_last_commit_seconds":blast,"btrfs_max_commit_seconds":bmax,
       "critical_candidate":candidate,"critical_streak":state["critical_streak"],"warning":warning,"would_recover":would}
    prev=bool(state.get("would_recover",False)); last=float(state.get("last_snapshot_at",0) or 0)
    if would and (not prev or now-last>=REPEAT):m["snapshot"]=snap(now_iso,m,flagged,"would-recover"); state["last_snapshot_at"]=now
    elif candidate and state["critical_streak"]==1:m["snapshot"]=snap(now_iso,m,flagged,"critical-candidate"); state["last_snapshot_at"]=now
    elif prev and not would:m["snapshot"]=snap(now_iso,m,flagged,"recovered"); state["last_snapshot_at"]=now
    state["would_recover"]=would; state["last_run"]=now_iso
    atomic(STATE,json.dumps(state,indent=2,sort_keys=True)+"\n"); atomic(CURRENT,json.dumps(m,indent=2,sort_keys=True)+"\n",0o644); print(json.dumps(m,sort_keys=True)); return 0
if __name__=="__main__":raise SystemExit(main())
