#!/usr/bin/env python3
import importlib.util
import json
import os
import tempfile
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

    current=tmp/"current.json"
    current.write_text(json.dumps({"timestamp":"2026-01-01T00:00:00+00:00","docker_up":True,"warning":True,"load5":1.5,"memory_used_ratio":0.4,
        "temperature_max_celsius":None,"ups_configured":True,"ups_data_fresh":False,"systemd_failed":{"count":2,"units":["x.service","y.service"]},
        "docker_inventory":{"ok":True,"oom_killed":1,"stopped_restartable":0}}))
    os.environ["INCIDENT_DIR"]=str(tmp/"incidents"); os.environ["WATCHDOG_STATE"]=str(current)
    obs=load("observer_agent",ROOT/"observer-agent"/"app.py")
    s=obs.watchdog_state()
    assert s["extended_available"]==1 and s["warning"]==1
    assert s["memory_used_ratio"]==0.4 and s["temperature_max_celsius"]==-1.0
    assert s["ups_configured"]==1 and s["ups_data_fresh"]==0
    assert s["systemd_failed"]==2 and s["docker_oom_killed"]==1

    current.write_text(json.dumps({"timestamp":"2026-01-01T00:00:00+00:00","docker_up":True}))
    assert obs.watchdog_state()["extended_available"]==0, "older watchdog state keeps host-health alerts gated off"

print("watchdog host-health signals: PASS")
