import json, os, re, subprocess, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BIND=os.getenv("OBSERVER_BIND","127.0.0.1"); PORT=int(os.getenv("OBSERVER_PORT","9199"))
INTERVAL=max(5,int(os.getenv("OBSERVER_INTERVAL_SECONDS","10")))
PROBES=Path(os.getenv("OBSERVER_PROBE_CONFIG","/config/probes.json"))
HOST_PROC=Path(os.getenv("HOST_PROC","/host/proc")); HOST_SYS=Path(os.getenv("HOST_SYS","/host/sys"))
WATCHDOG_EXPECTED=os.getenv("WATCHDOG_EXPECTED","0").lower() in {"1","true","yes"}
WATCHDOG_STATE=Path(os.getenv("WATCHDOG_STATE","/watchdog/current.json"))
INCIDENT_DIR=Path(os.getenv("INCIDENT_DIR","/data/incidents")); INCIDENT_DIR.mkdir(parents=True,exist_ok=True)
LOCK=threading.Lock(); CURRENT={"collected_at":0,"metrics":"","status":{}}

def run(cmd,timeout=5):
    start=time.monotonic()
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,check=False)
        return p.returncode,(p.stdout+p.stderr).strip(),time.monotonic()-start
    except subprocess.TimeoutExpired: return 124,"timeout",time.monotonic()-start
    except Exception as e: return 127,f"{type(e).__name__}: {e}",time.monotonic()-start

def read_text(path,limit=16000):
    try:return Path(path).read_text(errors="replace")[-limit:]
    except Exception as e:return f"{type(e).__name__}: {e}"

def load_probes():
    try:
        d=json.loads(PROBES.read_text()); return {k:list(d.get(k) or []) for k in ("icmp","dns","http")}
    except Exception:return {"icmp":[],"dns":[],"http":[]}

def process_states():
    counts={}; flagged=[]
    try: entries=HOST_PROC.iterdir()
    except Exception:return counts,flagged
    for p in entries:
        if not p.name.isdigit():continue
        try:
            raw=(p/"stat").read_text(errors="replace"); left,right=raw.find("("),raw.rfind(")")
            if left<0 or right<0 or right+2>=len(raw):continue
            state=raw[right+2:right+3]; comm=raw[left+1:right]; counts[state]=counts.get(state,0)+1
            if state in {"D","Z"}:
                try:wchan=(p/"wchan").read_text(errors="replace").strip()
                except Exception:wchan="?"
                flagged.append({"pid":int(p.name),"state":state,"comm":comm,"wchan":wchan})
        except Exception:pass
    return counts,flagged[:200]

def key_values(path,scale=1):
    out={}
    try:
        for line in Path(path).read_text().splitlines():
            parts=line.replace(":"," ").split()
            if len(parts)>=2:
                try:out[parts[0]]=int(parts[1])*scale
                except ValueError:pass
    except Exception:pass
    return out

def btrfs_stats():
    last=maxc=0; errors={}; base=HOST_SYS/"fs"/"btrfs"
    try:
        for p in base.glob("*/commit_stats"):
            for line in p.read_text().splitlines():
                parts=line.split()
                if len(parts)!=2:continue
                if parts[0]=="last_commit_ms":last=max(last,int(parts[1]))
                if parts[0]=="max_commit_ms":maxc=max(maxc,int(parts[1]))
        error_stat_files=list(base.glob("*/devinfo/*/error_stats"))
        if error_stat_files:
            for p in error_stat_files:
                try:
                    for line in p.read_text().splitlines():
                        parts=line.split()
                        if len(parts)!=2:continue
                        name=parts[0].removesuffix("_errs")
                        errors[name]=errors.get(name,0)+int(parts[1])
                except Exception:pass
        else:
            for p in base.glob("*/devices/*/stats/*"):
                try:errors[p.name]=errors.get(p.name,0)+int(p.read_text().strip())
                except Exception:pass
    except Exception:pass
    return last/1000.0,maxc/1000.0,errors

def watchdog_state():
    out={"expected":WATCHDOG_EXPECTED,"present":False,"age_seconds":-1.0,"docker_up":0,"would_recover":0,"critical_streak":0,
         "extended_available":0,"warning":0,"load5":0.0,"load15":0.0,"memory_used_ratio":-1.0,"temperature_max_celsius":-1.0,
         "filesystem_max_used_ratio":0.0,"temperature_warn_celsius":85.0,"filesystem_warn_ratio":0.90,"docker_inventory_ok":0,"mdraid_degraded":0,"mdraid_sync_active":0,"mdraid_sync_progress_percent":-1.0,
         "ups_configured":0,"ups_data_fresh":1,"ups_on_battery":0,"ups_low_battery":0,"systemd_failed":0,
         "docker_unhealthy":0,"docker_restarting":0,"docker_oom_killed":0,"docker_stopped_restartable":0,"probe_budget_exhausted":0}
    if not WATCHDOG_STATE.exists():return out
    out["present"]=True
    try:
        d=json.loads(WATCHDOG_STATE.read_text()); out["docker_up"]=1 if d.get("docker_up") else 0
        out["would_recover"]=1 if d.get("would_recover") else 0; out["critical_streak"]=float(d.get("critical_streak") or 0)
        if "docker_inventory" in d:
            # Host-health fields are only present in watchdog state written by a release that collects them.
            out["extended_available"]=1; out["warning"]=1 if d.get("warning") else 0
            for key in ("load5","load15","filesystem_max_used_ratio","docker_unhealthy","docker_restarting"):out[key]=float(d.get(key) or 0)
            for key in ("temperature_warn_celsius","filesystem_warn_ratio"):
                if d.get(key) is not None:out[key]=float(d[key])
            for key in ("memory_used_ratio","temperature_max_celsius","mdraid_sync_progress_percent"):
                if d.get(key) is not None:out[key]=float(d[key])
            for key in ("mdraid_degraded","mdraid_sync_active","ups_configured","ups_on_battery","ups_low_battery","probe_budget_exhausted"):out[key]=1 if d.get(key) else 0
            # -1 marks freshness unknown (UPS reads cut by the run budget); the stale alert only matches 0.
            fresh=d.get("ups_data_fresh"); out["ups_data_fresh"]=0 if fresh is False else (-1 if fresh is None and d.get("ups_configured") else 1)
            out["systemd_failed"]=float((d.get("systemd_failed") or {}).get("count") or 0)
            inv=d.get("docker_inventory") or {}; out["docker_inventory_ok"]=1 if inv.get("ok") else 0
            out["docker_oom_killed"]=float(inv.get("oom_killed") or 0); out["docker_stopped_restartable"]=float(inv.get("stopped_restartable") or 0)
        ts=d.get("timestamp")
        if ts:
            then=datetime.fromisoformat(str(ts).replace("Z","+00:00")); out["age_seconds"]=max(0,(datetime.now(timezone.utc)-then).total_seconds())
        else:out["age_seconds"]=max(0,time.time()-WATCHDOG_STATE.stat().st_mtime)
    except Exception:out["age_seconds"]=max(0,time.time()-WATCHDOG_STATE.stat().st_mtime)
    return out

def probe_icmp(target):
    rc,out,elapsed=run(["ping","-c","1","-W","2",target],3); m=re.search(r"time[=<]([0-9.]+)\s*ms",out)
    return rc==0,(float(m.group(1))/1000.0 if m else elapsed)

def probe_dns(item):
    server=str(item if isinstance(item,str) else item.get("server","")); name="example.com" if isinstance(item,str) else str(item.get("name","example.com"))
    rc,_,elapsed=run(["dig","+time=2","+tries=1",f"@{server}",name,"A"],3); return rc==0,elapsed,server

def probe_http(url):
    rc,out,elapsed=run(["curl","-sS","--max-time","4","-o","/dev/null","-w","%{http_code} %{time_total}",url],5)
    parts=out.split(); ok=False; duration=elapsed
    if len(parts)>=2:
        try:code=int(parts[-2]); duration=float(parts[-1]); ok=rc==0 and 200<=code<400
        except ValueError:pass
    return ok,duration

def esc(v):return str(v).replace("\\","\\\\").replace('"','\\"').replace("\n","\\n")
def metric(name,value,labels=None):
    if labels:return f'{name}{{'+",".join(f'{k}="{esc(v)}"' for k,v in sorted(labels.items()))+f'}} {value}'
    return f"{name} {value}"

def collect():
    now=time.time()
    try:load1=float((HOST_PROC/"loadavg").read_text().split()[0])
    except Exception:load1=0.0
    counts,flagged=process_states(); mem=key_values(HOST_PROC/"meminfo",1024); vm=key_values(HOST_PROC/"vmstat")
    st,sf=mem.get("SwapTotal",0),mem.get("SwapFree",0); sr=0.0 if st<=0 else max(0,min(1,(st-sf)/st))
    blast,bmax,berrors=btrfs_stats(); wd=watchdog_state(); cfg=load_probes(); probes=[]
    for t in cfg["icmp"]:
        ok,d=probe_icmp(str(t)); probes.append({"kind":"icmp","target":str(t),"success":ok,"duration":d})
    for item in cfg["dns"]:
        ok,d,t=probe_dns(item); probes.append({"kind":"dns","target":t,"success":ok,"duration":d})
    for t in cfg["http"]:
        ok,d=probe_http(str(t)); probes.append({"kind":"http","target":str(t),"success":ok,"duration":d})
    lines=[metric("observer_agent_up",1),metric("observer_agent_last_collection_unixtime",f"{now:.3f}"),metric("observer_host_load1",load1),
      metric("observer_host_procs_blocked",counts.get("D",0)),metric("observer_host_zombies",counts.get("Z",0)),
      metric("observer_host_swap_total_bytes",st),metric("observer_host_swap_free_bytes",sf),metric("observer_host_swap_used_ratio",f"{sr:.6f}"),
      metric("observer_vmstat_pswpin_total",vm.get("pswpin",0)),metric("observer_vmstat_pswpout_total",vm.get("pswpout",0)),
      metric("observer_btrfs_last_commit_seconds",f"{blast:.6f}"),metric("observer_btrfs_max_commit_seconds",f"{bmax:.6f}"),
      metric("observer_watchdog_expected",1 if wd["expected"] else 0),metric("observer_watchdog_present",1 if wd["present"] else 0),
      metric("observer_watchdog_age_seconds",f"{wd['age_seconds']:.3f}"),metric("observer_watchdog_docker_up",wd["docker_up"]),
      metric("observer_watchdog_would_recover",wd["would_recover"]),metric("observer_watchdog_critical_streak",wd["critical_streak"])]
    for key in ("extended_available","warning","load5","load15","memory_used_ratio","temperature_max_celsius","filesystem_max_used_ratio",
                "mdraid_degraded","mdraid_sync_active","mdraid_sync_progress_percent","ups_configured","ups_data_fresh","ups_on_battery",
                "ups_low_battery","systemd_failed","temperature_warn_celsius","filesystem_warn_ratio","docker_inventory_ok","docker_unhealthy","docker_restarting","docker_oom_killed","docker_stopped_restartable","probe_budget_exhausted"):
        lines.append(metric(f"observer_watchdog_{key}",wd[key]))
    for typ,val in sorted(berrors.items()):lines.append(metric("observer_btrfs_device_errors_total",val,{"type":typ}))
    for p in probes:
        labels={"kind":p["kind"],"target":p["target"]}; lines.append(metric("observer_probe_success",1 if p["success"] else 0,labels)); lines.append(metric("observer_probe_duration_seconds",f"{p['duration']:.6f}",labels))
    status={"collected_at":datetime.now(timezone.utc).isoformat(),"load1":load1,"dstate":counts.get("D",0),"zombies":counts.get("Z",0),
      "swap_used_ratio":sr,"btrfs_last_commit_seconds":blast,"watchdog":wd,"probes":probes,"blocked_or_zombie":flagged}
    return "\n".join(lines)+"\n",status

def collector_loop():
    while True:
        try:
            metrics,status=collect()
            with LOCK:CURRENT.update({"collected_at":time.time(),"metrics":metrics,"status":status})
        except Exception as e:
            with LOCK:CURRENT["status"]={"error":f"{type(e).__name__}: {e}"}
        time.sleep(INTERVAL)

def snapshot(payload):
    now=datetime.now(timezone.utc)
    with LOCK:status=json.loads(json.dumps(CURRENT.get("status") or {}))
    alerts=payload.get("alerts") or []; names=sorted({str(a.get("labels",{}).get("alertname","unknown")) for a in alerts})
    diag={"route":run(["ip","route"],3),"neigh":run(["ip","neigh"],3),"host_loadavg":read_text(HOST_PROC/"loadavg"),
          "host_meminfo":read_text(HOST_PROC/"meminfo"),"host_vmstat":read_text(HOST_PROC/"vmstat"),"host_diskstats":read_text(HOST_PROC/"diskstats"),"host_mdstat":read_text(HOST_PROC/"mdstat")}
    serial={}
    for name,val in diag.items():
        serial[name]={"rc":val[0],"output":val[1],"duration_seconds":val[2]} if isinstance(val,tuple) else {"output":val}
    rec={"timestamp_utc":now.isoformat(),"status":str(payload.get("status","unknown")),"alerts":names,"observer":status,"diagnostics":serial}
    safe=re.sub(r"[^A-Za-z0-9_-]","-",rec["status"])[:32] or "unknown"; stem=f"{now.strftime('%Y%m%dT%H%M%SZ')}-{safe}"
    (INCIDENT_DIR/f"{stem}.json").write_text(json.dumps(rec,indent=2,sort_keys=True)+"\n"); return stem

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):print(fmt%args,flush=True)
    def do_GET(self):
        if self.path=="/healthz":
            with LOCK:col=CURRENT.get("collected_at",0)
            ok=bool(col and time.time()-col<INTERVAL*3); self.send_response(200 if ok else 503); self.end_headers(); self.wfile.write(("ok\n" if ok else "stale\n").encode()); return
        if self.path=="/metrics":
            with LOCK:body=CURRENT.get("metrics","")
            self.send_response(200); self.send_header("Content-Type","text/plain; version=0.0.4"); self.end_headers(); self.wfile.write(body.encode()); return
        if self.path=="/status":
            with LOCK:body=json.dumps(CURRENT.get("status") or {},indent=2,sort_keys=True)+"\n"
            self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(body.encode()); return
        self.send_response(404); self.end_headers()
    def do_POST(self):
        if self.path!="/alert":self.send_response(404); self.end_headers(); return
        try:
            length=int(self.headers.get("Content-Length","0"))
            if length>1024*1024:raise ValueError("request too large")
            stem=snapshot(json.loads(self.rfile.read(length) or b"{}")); self.send_response(200); self.end_headers(); self.wfile.write((stem+"\n").encode())
        except Exception as e:
            print(f"snapshot error: {type(e).__name__}: {e}",flush=True); self.send_response(500); self.end_headers()

def main():
    metrics,status=collect()
    with LOCK:CURRENT.update({"collected_at":time.time(),"metrics":metrics,"status":status})
    threading.Thread(target=collector_loop,daemon=True).start(); ThreadingHTTPServer((BIND,PORT),Handler).serve_forever()

if __name__=="__main__":main()
