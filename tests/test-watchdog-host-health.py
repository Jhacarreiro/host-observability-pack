#!/usr/bin/env python3
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp); os.environ["WATCHDOG_RUNTIME_DIR"]=str(tmp/"runtime"); os.environ["WATCHDOG_CGROUP_ROOT"]=str(tmp/"cgroup")
    wd=load("watchdog",ROOT/"watchdog"/"watchdog.py")

    def kills(cid,n):
        d=tmp/"cgroup"/"system.slice"/f"docker-{cid}.scope"; d.mkdir(parents=True,exist_ok=True)
        (d/"memory.events").write_text(f"low 0\nhigh 0\nmax 3\noom {n}\noom_kill {n}\n")
    def assess(state,now,*containers):
        inv={"problems":[],"containers":[{"id":c,"name":c,"status":s,"policy":p,"oom_flag":f} for c,s,p,f in containers]}
        wd.oom_assess(inv,state,now); return inv

    state={}
    kills("a",1)
    inv=assess(state,1000,("a","running","always",True))
    assert inv["oom_killed"]==0, "a kill that happened before the first run must not alert"
    assert inv["oom_recent"][0]["oom_flag"] is True
    kills("a",2)
    assert assess(state,1060,("a","running","always",True))["oom_killed"]==0, "one isolated kill must not alert"
    kills("a",3)
    assert assess(state,1120,("a","running","always",True))["oom_killed"]==1, "a second kill inside the window alerts"
    assert assess(state,1120+wd.OOM_WINDOW,("a","running","always",True))["oom_killed"]==0, "the alert clears once kills leave the window"
    assert assess(state,5000,("a","exited","always",True))["oom_killed"]==1, "a restartable container stopped by OOM alerts"
    assert assess(state,5000,("a","exited","no",True))["oom_killed"]==0, "a container without restart policy does not"
    kills("a",1)
    inv=assess(state,6000,("a","running","always",False))
    assert state["oom_tracking"]["a"]["events"]==[6000], "a restarted container's fresh counter is counted"
    kills("b",0)
    assess(state,6060,("b","running","no",False))
    assert sorted(state["oom_tracking"])==["b"], "removed containers are dropped from tracking"

    v1=tmp/"cgroup"/"memory"/"system.slice"/"docker-c.scope"; v1.mkdir(parents=True)
    (v1/"memory.oom_control").write_text("oom_kill_disable 0\nunder_oom 0\noom_kill 4\n")
    assert wd.cgroup_oom_kills("c")==4, "cgroup v1 systemd layout is read"

    def container(name,status,policy,code):
        return {"Id":name,"Name":"/"+name,"State":{"Status":status,"ExitCode":code,"OOMKilled":False},"HostConfig":{"RestartPolicy":{"Name":policy}}}
    real_run=wd.run
    fake=[container("crashed","exited","always",1),container("oneshot","exited","on-failure",0),
          container("stopped","exited","unless-stopped",143),container("manual","exited","no",137)]
    wd.run=lambda cmd,timeout=None:(0,"\n".join(c["Id"] for c in fake),"") if cmd[1]=="ps" else (0,json.dumps(fake),"")
    inv=wd.docker_inventory()
    assert inv["ok"] and inv["stopped_restartable"]==1, "only the unexpected non-zero exit with a restart policy counts"
    wd.run=lambda cmd,timeout=None:(1,"","Cannot connect")
    assert wd.docker_inventory()["ok"] is False

    wd.run=real_run
    upsc=tmp/"upsc"; upsc.write_text('#!/bin/sh\nif [ "$1" = -l ]; then printf "a\\nb\\nc\\n"; else sleep 5; fi\n'); upsc.chmod(0o755)
    wd.UPSC=str(upsc); wd.RUN_BUDGET=1.5; wd.START=time.monotonic(); wd.SKIPPED.clear()
    started=time.monotonic(); u=wd.ups(); elapsed=time.monotonic()-started
    assert elapsed<2.5, f"probes stop at the run budget, took {elapsed:.1f}s"
    assert u["configured"] and u["data_fresh"] is None, "UPS reads cut by the budget are unknown, not stale"
    assert wd.SKIPPED and wd.run(["true"])[0]==125, "probes past the budget are skipped and recorded"

    current=tmp/"current.json"
    current.write_text(json.dumps({"timestamp":"2026-01-01T00:00:00+00:00","docker_up":True,"warning":True,"load5":1.5,"memory_used_ratio":0.4,
        "temperature_max_celsius":None,"ups_configured":True,"ups_data_fresh":False,"systemd_failed":{"count":2,"units":["x.service","y.service"]},
        "temperature_warn_celsius":70,"docker_inventory":{"ok":True,"oom_killed":1,"stopped_restartable":0}}))
    os.environ["INCIDENT_DIR"]=str(tmp/"incidents"); os.environ["WATCHDOG_STATE"]=str(current)
    obs=load("observer_agent",ROOT/"observer-agent"/"app.py")
    s=obs.watchdog_state()
    assert s["extended_available"]==1 and s["warning"]==1
    assert s["memory_used_ratio"]==0.4 and s["temperature_max_celsius"]==-1.0
    assert s["ups_configured"]==1 and s["ups_data_fresh"]==0
    assert s["systemd_failed"]==2 and s["docker_oom_killed"]==1
    assert s["temperature_warn_celsius"]==70 and s["docker_inventory_ok"]==1

    current.write_text(json.dumps({"timestamp":"2026-01-01T00:00:00+00:00","docker_up":True,"docker_inventory":{"ok":False,"error":"x"}}))
    s=obs.watchdog_state()
    assert s["extended_available"]==1 and s["docker_inventory_ok"]==0, "a failed inventory is exported, not hidden as healthy zeros"

    current.write_text(json.dumps({"timestamp":"2026-01-01T00:00:00+00:00","docker_up":True}))
    assert obs.watchdog_state()["extended_available"]==0, "older watchdog state keeps host-health alerts gated off"

print("watchdog host-health signals: PASS")
