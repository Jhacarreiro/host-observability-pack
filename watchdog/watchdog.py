#!/usr/bin/env python3
import fcntl, glob, json, os, re, shutil, subprocess, tempfile, time
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
TEMP_WARN=ef("WATCHDOG_TEMP_WARN_C",85); FS_WARN=ef("WATCHDOG_FILESYSTEM_WARN_RATIO",.90); PROBE_TIMEOUT=ef("WATCHDOG_PROBE_TIMEOUT_SECONDS",3)
FS_PATHS=[x.strip() for x in os.getenv("WATCHDOG_FILESYSTEMS","/").split(",") if x.strip()]
UPSC=os.getenv("WATCHDOG_UPSC_BIN","").strip() or shutil.which("upsc") or ""
SYSTEMCTL=os.getenv("WATCHDOG_SYSTEMCTL_BIN","").strip() or shutil.which("systemctl") or ""
CGROUP_ROOT=Path(os.getenv("WATCHDOG_CGROUP_ROOT","/sys/fs/cgroup")); OOM_WINDOW=ei("WATCHDOG_OOM_WINDOW_SECONDS",1800); OOM_KILLS=ei("WATCHDOG_OOM_RECURRING_KILLS",2)
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

def meminfo():
    vals={}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if ":" not in line:continue
        k,v=line.split(":",1)
        try:vals[k]=int(v.strip().split()[0])*1024
        except Exception:pass
    return vals

def swap_ratio(vals):
    t,f=vals.get("SwapTotal",0),vals.get("SwapFree",0); return 0 if t<=0 else max(0,min(1,(t-f)/t))

def memory_used_ratio(vals):
    t=vals.get("MemTotal",0); a=vals.get("MemAvailable",vals.get("MemFree",0)); return 0 if t<=0 else max(0,min(1,(t-a)/t))

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

def run(cmd,timeout=None):
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout or PROBE_TIMEOUT,check=False); return p.returncode,p.stdout,p.stderr
    except subprocess.TimeoutExpired:return 124,"","timeout"
    except Exception as e:return 127,"",f"{type(e).__name__}: {e}"

def temperature_max():
    vals=[]
    for f in glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
        try:raw=float(Path(f).read_text().strip()); vals.append(raw/1000 if abs(raw)>500 else raw)
        except Exception:pass
    return round(max(vals),2) if vals else None

def filesystems():
    items=[]; seen=set()
    for path in FS_PATHS:
        try:
            st=os.statvfs(path); total=st.f_blocks*st.f_frsize; key=(st.f_fsid,total)
            if key in seen:continue
            seen.add(key); used=0 if total<=0 else max(0,min(1,(total-st.f_bavail*st.f_frsize)/total))
            items.append({"path":path,"used_ratio":round(used,6)})
        except Exception as e:items.append({"path":path,"error":f"{type(e).__name__}: {e}"})
    ratios=[x["used_ratio"] for x in items if "used_ratio" in x]; return items,(max(ratios) if ratios else 0.0)

def mdraid():
    text=bounded("/proc/mdstat"); m=re.search(r"(resync|recovery|reshape)\s*=\s*([0-9.]+)%",text)
    return any("_" in x for x in re.findall(r"\[[U_]+\]",text)),bool(m),(float(m.group(2)) if m else None)

def ups():
    out={"configured":False,"data_fresh":None,"on_battery":False,"low_battery":False}
    if not UPSC:return out
    rc,listing,_=run([UPSC,"-l"]); devices=[x.strip() for x in listing.splitlines() if x.strip()] if rc==0 else []
    fresh=True; tokens=set()
    for dev in devices:
        drc,dout,_=run([UPSC,dev])
        if drc!=0:fresh=False; continue
        for line in dout.splitlines():
            if line.startswith("ups.status:"):tokens.update(line.split(":",1)[1].split())
    out.update({"configured":bool(devices),"data_fresh":fresh if devices else None,"on_battery":"OB" in tokens,"low_battery":"LB" in tokens}); return out

def systemd_failed():
    if not SYSTEMCTL:return {"count":None,"units":[]}
    rc,out,_=run([SYSTEMCTL,"--failed","--no-legend","--no-pager","--plain"])
    units=[x.split()[0] for x in out.splitlines() if x.split()] if rc==0 else []
    return {"count":len(units) if rc==0 else None,"units":units}

def docker_inventory():
    inv={"ok":False,"total":0,"running":0,"unhealthy":0,"restarting":0,"oom_killed":0,"stopped_restartable":0,"problems":[],"containers":[]}
    rc,out,err=run([DOCKER,"ps","-aq"],DOCKER_TIMEOUT); ids=[x.strip() for x in out.splitlines() if x.strip()]
    if rc!=0:inv["error"]=(err or out).strip()[-1000:]; return inv
    objs=[]
    if ids:
        rc,out,err=run([DOCKER,"inspect"]+ids,DOCKER_TIMEOUT)
        if rc!=0:inv["error"]=(err or out).strip()[-1000:]; return inv
        try:objs=json.loads(out)
        except Exception as e:inv["error"]=f"json: {type(e).__name__}: {e}"; return inv
    inv["ok"]=True; inv["total"]=len(objs)
    for obj in objs:
        st=obj.get("State") or {}; status=str(st.get("Status") or "unknown"); health=str((st.get("Health") or {}).get("Status") or "none")
        policy=str(((obj.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or "no"); name=str(obj.get("Name") or "").lstrip("/")
        inv["running"]+=status=="running"; inv["restarting"]+=status=="restarting"
        if status=="running" and health=="unhealthy":inv["unhealthy"]+=1
        # Exit 0 is a finished one-shot (on-failure does not restart it) and 143 is a graceful `docker stop`;
        # neither is an unexpected stop.
        exit_code=int(st.get("ExitCode") or 0)
        if status not in {"running","restarting","created"} and policy not in {"","no"} and exit_code not in {0,143}:inv["stopped_restartable"]+=1
        if (status=="running" and health=="unhealthy") or status=="restarting":inv["problems"].append({"name":name,"status":status,"health":health,"restart_policy":policy})
        inv["containers"].append({"id":str(obj.get("Id") or ""),"name":name,"status":status,"policy":policy,"oom_flag":bool(st.get("OOMKilled"))})
    return inv

def cgroup_oom_kills(cid):
    # cgroup v2 exposes memory.events and cgroup v1 memory.oom_control, each under the systemd or cgroupfs driver layout.
    for path in (CGROUP_ROOT/"system.slice"/f"docker-{cid}.scope"/"memory.events",CGROUP_ROOT/"docker"/cid/"memory.events",
                 CGROUP_ROOT/"memory"/"system.slice"/f"docker-{cid}.scope"/"memory.oom_control",CGROUP_ROOT/"memory"/"docker"/cid/"memory.oom_control"):
        try:
            for line in path.read_text().splitlines():
                k,_,v=line.partition(" ")
                if k=="oom_kill":return int(v)
        except Exception:continue
    return None

def oom_assess(inv,state,now):
    """Count containers with an actionable OOM condition.

    Docker keeps State.OOMKilled=true until the container restarts, even after a single
    child process was killed once, so the flag alone alerts forever. New kills are counted
    from the cgroup oom_kill counter instead. A container is active only with OOM_KILLS
    kills inside OOM_WINDOW seconds, or when a restartable container was left stopped by an
    OOM. Isolated kills are listed in oom_recent without alerting.
    """
    first=("oom_tracking" not in state); prev=state.get("oom_tracking") or {}; tracking={}; recent=[]; active=0
    for c in inv.pop("containers",[]):
        old=prev.get(c["id"]) or {}; events=[t for t in old.get("events",[]) if now-t<OOM_WINDOW]
        count=cgroup_oom_kills(c["id"]) if c["status"]=="running" else None
        if count is not None:
            base=old.get("count")
            # The first run baselines existing counters; a restarted container gets a fresh counter.
            new=(0 if first else count) if base is None else (count-base if count>=base else count)
            events+=[now]*new
        stopped=c["oom_flag"] and c["status"] not in {"running","restarting"} and c["policy"] not in {"","no"}
        is_active=stopped or len(events)>=OOM_KILLS
        tracking[c["id"]]={"name":c["name"],"count":count,"events":events}
        if events or c["oom_flag"]:recent.append({"name":c["name"],"status":c["status"],"oom_flag":c["oom_flag"],"kills_in_window":len(events),"active":is_active})
        if is_active:
            active+=1; inv["problems"].append({"name":c["name"],"status":c["status"],"restart_policy":c["policy"],"oom_kills_in_window":len(events),"stopped_by_oom":stopped})
    state["oom_tracking"]=tracking; inv["oom_killed"]=active; inv["oom_recent"]=recent

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
    now=time.time(); now_iso=datetime.now(timezone.utc).isoformat(); load1,load5,load15=(float(x) for x in Path("/proc/loadavg").read_text().split()[:3])
    counts,flagged=proc_states(); d=counts.get("D",0); z=counts.get("Z",0); mem=meminfo(); swap=swap_ratio(mem); blast,bmax=btrfs(); dup,dlat,drc=docker_probe()
    temp=temperature_max(); fs_items,fs_max=filesystems(); md_degraded,md_sync,md_progress=mdraid(); ups_state=ups(); failed=systemd_failed()
    inv=docker_inventory() if dup else {"ok":False,"problems":[],"error":"docker unavailable"}
    state=load_json(STATE,{"critical_streak":0,"docker_fail_streak":0,"would_recover":False,"last_snapshot_at":0})
    state["docker_fail_streak"]=0 if dup else int(state.get("docker_fail_streak",0))+1
    if inv["ok"]:oom_assess(inv,state,now)
    candidate=(d>=D_HARD and load1>=LOAD_HARD) or (blast>=BTRFS_WARN and d>=D_WARN) or ((not dup) and d>=D_WARN and load1>=LOAD_HARD)
    state["critical_streak"]=int(state.get("critical_streak",0))+1 if candidate else 0; would=state["critical_streak"]>=STREAK
    docker_warning=bool(inv.get("unhealthy") or inv.get("restarting") or inv.get("oom_killed"))
    warning=(d>=D_WARN or load1>=LOAD_WARN or swap>=SWAP_WARN or not dup or dlat>=DOCKER_WARN or blast>=BTRFS_WARN or
             (temp is not None and temp>=TEMP_WARN) or fs_max>=FS_WARN or md_degraded or ups_state["data_fresh"] is False or
             ups_state["on_battery"] or bool(failed["count"]) or docker_warning)
    m={"timestamp":now_iso,"mode":MODE,"load1":load1,"load5":load5,"load15":load15,"dstate":d,"zombies":z,"swap_used_ratio":round(swap,6),
       "memory_used_ratio":round(memory_used_ratio(mem),6),"temperature_max_celsius":temp,"filesystems":fs_items,"filesystem_max_used_ratio":fs_max,
       "temperature_warn_celsius":TEMP_WARN,"filesystem_warn_ratio":FS_WARN,"mdraid_degraded":md_degraded,"mdraid_sync_active":md_sync,"mdraid_sync_progress_percent":md_progress,
       "ups_configured":ups_state["configured"],"ups_data_fresh":ups_state["data_fresh"],"ups_on_battery":ups_state["on_battery"],"ups_low_battery":ups_state["low_battery"],
       "systemd_failed":failed,"docker_inventory":inv,"docker_unhealthy":int(inv.get("unhealthy") or 0),"docker_restarting":int(inv.get("restarting") or 0),
       "docker_up":dup,"docker_latency_seconds":round(dlat,4),
       "docker_rc":drc,"docker_fail_streak":state["docker_fail_streak"],"btrfs_last_commit_seconds":blast,"btrfs_max_commit_seconds":bmax,
       "critical_candidate":candidate,"critical_streak":state["critical_streak"],"warning":warning,"would_recover":would}
    prev=bool(state.get("would_recover",False)); last=float(state.get("last_snapshot_at",0) or 0)
    if would and (not prev or now-last>=REPEAT):m["snapshot"]=snap(now_iso,m,flagged,"would-recover"); state["last_snapshot_at"]=now
    elif candidate and state["critical_streak"]==1:m["snapshot"]=snap(now_iso,m,flagged,"critical-candidate"); state["last_snapshot_at"]=now
    elif prev and not would:m["snapshot"]=snap(now_iso,m,flagged,"recovered"); state["last_snapshot_at"]=now
    state["would_recover"]=would; state["last_run"]=now_iso
    atomic(STATE,json.dumps(state,indent=2,sort_keys=True)+"\n"); atomic(CURRENT,json.dumps(m,indent=2,sort_keys=True)+"\n",0o644); print(json.dumps(m,sort_keys=True)); return 0
if __name__=="__main__":raise SystemExit(main())
