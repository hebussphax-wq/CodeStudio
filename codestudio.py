#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from core import CodeStudioCore

ROOT = pathlib.Path(__file__).resolve().parent
CFG_PATH = ROOT / "config.json"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CodeStudio – lokale KI-Mitarbeiter")
        self.root.geometry("1180x820")
        self.root.minsize(940, 680)
        self.cfg = json.loads(CFG_PATH.read_text(encoding="utf-8-sig"))
        self.q = queue.Queue()
        self.current_result = None
        w = pathlib.Path(self.cfg["workspace"])
        self.workspace_var = tk.StringVar(value=str((ROOT / w).resolve() if not w.is_absolute() else w.resolve()))
        self.model_var = tk.StringVar(value=self.cfg["roles"]["coder"]["model"])
        self.status_var = tk.StringVar(value="Bereit")
        self.core = CodeStudioCore(ROOT, self.cfg, log=self.enqueue_log)
        self.core.set_workspace(self.workspace_var.get())
        self.build_ui()
        self.refresh_models()
        self.root.after(100, self.poll)

    def build_ui(self):
        top = ttk.Frame(self.root, padding=10); top.pack(fill="x")
        ttk.Label(top, text="Workspace").grid(row=0,column=0,sticky="w")
        ttk.Entry(top, textvariable=self.workspace_var).grid(row=0,column=1,sticky="ew",padx=6)
        ttk.Button(top, text="Wählen…", command=self.choose_workspace).grid(row=0,column=2)
        ttk.Label(top, text="Ollama-Modell").grid(row=1,column=0,sticky="w",pady=(8,0))
        self.model_box = ttk.Combobox(top, textvariable=self.model_var, state="normal")
        self.model_box.grid(row=1,column=1,sticky="ew",padx=6,pady=(8,0))
        ttk.Button(top, text="Modelle neu laden", command=self.refresh_models).grid(row=1,column=2,pady=(8,0))
        top.columnconfigure(1, weight=1)

        tf = ttk.LabelFrame(self.root, text="Aufgabe", padding=8); tf.pack(fill="x",padx=10,pady=(0,8))
        self.task = ScrolledText(tf, height=5, wrap="word"); self.task.pack(fill="x")

        bf = ttk.Frame(self.root, padding=(10,0,10,8)); bf.pack(fill="x")
        self.analyze_btn = ttk.Button(bf,text="Analysieren",command=self.analyze); self.analyze_btn.pack(side="left")
        self.apply_btn = ttk.Button(bf,text="Änderungen anwenden",command=self.apply,state="disabled"); self.apply_btn.pack(side="left",padx=6)
        ttk.Button(bf,text="Diff verwerfen",command=self.reject).pack(side="left")
        ttk.Label(bf,textvariable=self.status_var).pack(side="right")

        paned = ttk.Panedwindow(self.root, orient="vertical"); paned.pack(fill="both",expand=True,padx=10,pady=(0,10))
        upper = ttk.Panedwindow(paned, orient="horizontal"); paned.add(upper, weight=3)
        pf = ttk.LabelFrame(upper,text="Plan"); self.plan_text=ScrolledText(pf,wrap="word"); self.plan_text.pack(fill="both",expand=True); upper.add(pf,weight=1)
        df = ttk.LabelFrame(upper,text="Diff"); self.diff_text=ScrolledText(df,wrap="none",font=("Consolas",10)); self.diff_text.pack(fill="both",expand=True); upper.add(df,weight=2)
        lf = ttk.LabelFrame(paned,text="Ablauf / Log"); self.log_text=ScrolledText(lf,height=10,wrap="word"); self.log_text.pack(fill="both",expand=True); paned.add(lf,weight=1)

    def enqueue_log(self,text): self.q.put(("log",text))
    def poll(self):
        try:
            while True:
                kind,payload=self.q.get_nowait()
                if kind=="log": self.log_text.insert("end",payload+"\n"); self.log_text.see("end")
                elif kind=="analysis_ok": self.on_analysis_ok(payload)
                elif kind=="analysis_error": self.on_analysis_error(payload)
                elif kind=="apply_ok": self.on_apply_ok(payload)
                elif kind=="apply_error": self.on_apply_error(payload)
        except queue.Empty: pass
        self.root.after(100,self.poll)

    def choose_workspace(self):
        p=filedialog.askdirectory(initialdir=self.workspace_var.get())
        if p: self.workspace_var.set(p); self.core.set_workspace(p)

    def refresh_models(self):
        def worker():
            try:
                models=self.core.installed_models(); self.q.put(("log","Ollama-Modelle: "+(", ".join(models) if models else "keine")))
                self.root.after(0,lambda:self.model_box.configure(values=models))
                if models and self.model_var.get() not in models: self.root.after(0,lambda:self.model_var.set(models[0]))
            except Exception as exc: self.q.put(("log",f"Ollama: {exc}"))
        threading.Thread(target=worker,daemon=True).start()

    def analyze(self):
        task=self.task.get("1.0","end").strip()
        if not task: messagebox.showwarning("CodeStudio","Bitte eine Aufgabe eingeben."); return
        self.core.set_workspace(self.workspace_var.get()); self.analyze_btn.configure(state="disabled"); self.apply_btn.configure(state="disabled"); self.current_result=None
        self.plan_text.delete("1.0","end"); self.diff_text.delete("1.0","end"); self.status_var.set("KI-Mitarbeiter arbeiten …")
        model=self.model_var.get().strip()
        def worker():
            try: self.q.put(("analysis_ok",self.core.analyze(task,model)))
            except Exception as exc: self.q.put(("analysis_error",str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def on_analysis_ok(self,result):
        self.current_result=result; self.plan_text.delete("1.0","end")
        for i,s in enumerate(result.plan.get("plan",[]),1): self.plan_text.insert("end",f"{i}. {s}\n")
        if result.plan.get("acceptance"):
            self.plan_text.insert("end","\nAkzeptanz:\n")
            for x in result.plan["acceptance"]: self.plan_text.insert("end",f"• {x}\n")
        self.diff_text.delete("1.0","end"); self.diff_text.insert("1.0",result.diff)
        self.apply_btn.configure(state="normal"); self.analyze_btn.configure(state="normal"); self.status_var.set("Diff bereit – prüfen und anwenden")

    def on_analysis_error(self,msg):
        self.analyze_btn.configure(state="normal"); self.status_var.set("Fehler"); messagebox.showerror("Analyse fehlgeschlagen",msg)

    def apply(self):
        if not self.current_result: return
        if not messagebox.askyesno("CodeStudio","Gezeigte Änderungen jetzt anwenden?"): return
        self.apply_btn.configure(state="disabled"); self.analyze_btn.configure(state="disabled"); self.status_var.set("Schreibe Änderungen / Tests …")
        def worker():
            try: self.q.put(("apply_ok",self.core.apply(self.current_result)))
            except Exception as exc: self.q.put(("apply_error",str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def on_apply_ok(self,receipt):
        self.analyze_btn.configure(state="normal"); status=receipt.get("status","unbekannt"); self.status_var.set(status)
        if status=="applied": messagebox.showinfo("CodeStudio","Änderungen erfolgreich angewendet.")
        elif status=="rolled-back": messagebox.showwarning("Tests fehlgeschlagen","Änderungen wurden automatisch zurückgerollt.\n\n"+(receipt.get("test") or {}).get("output","")[-2000:])
        else: messagebox.showwarning("CodeStudio",f"Status: {status}")

    def on_apply_error(self,msg):
        self.analyze_btn.configure(state="normal"); self.status_var.set("Fehler / Rollback"); messagebox.showerror("Anwenden fehlgeschlagen",msg)

    def reject(self):
        self.current_result=None; self.apply_btn.configure(state="disabled"); self.diff_text.delete("1.0","end"); self.status_var.set("Diff verworfen")


def doctor(cfg):
    core=CodeStudioCore(ROOT,cfg,log=print)
    print("=== CodeStudio Doctor ==="); print("Python:",sys.version.split()[0]); print("Workspace:",core.workspace)
    try:
        models=core.installed_models(); print("Ollama API: OK")
        for m in models: print(" -",m)
    except Exception as exc: print("Ollama API: FEHLER:",exc); return 1
    if shutil.which("nvidia-smi"):
        p=subprocess.run(["nvidia-smi","--query-gpu=name,memory.total,driver_version","--format=csv,noheader"],capture_output=True,text=True,check=False)
        print("NVIDIA:",p.stdout.strip() or p.stderr.strip())
    else: print("NVIDIA: nvidia-smi nicht gefunden")
    return 0


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--doctor",action="store_true"); args=ap.parse_args()
    cfg=json.loads(CFG_PATH.read_text(encoding="utf-8-sig"))
    if args.doctor: raise SystemExit(doctor(cfg))
    root=tk.Tk(); App(root); root.mainloop()

if __name__=="__main__": main()
