#!/usr/bin/env python3
"""Standalone GUI for the same proposal engine used by TobyKi."""
from __future__ import annotations
import argparse
import copy
import json
import os
import pathlib
import queue
import sys
import threading
from core import CodeStudioCore
from safety import atomic_bytes, canonical, redact

ROOT = pathlib.Path(__file__).resolve().parent

def load_config(file=None, workspace=None, state_dir=None):
    cfg = json.loads(pathlib.Path(file or ROOT / 'config.json').read_text(encoding='utf-8-sig'))
    state = pathlib.Path(state_dir or os.environ.get('LOCALAPPDATA', pathlib.Path.home()) / pathlib.Path('CodeStudio'))
    cfg['state_dir'] = str(state)
    if workspace:
        cfg['workspace'] = workspace
    elif not file:
        settings = state / 'preferences.json'
        if settings.is_file():
            saved = json.loads(settings.read_text(encoding='utf-8'))
            for key in ('workspace', 'selected_model', 'tests', 'ollama_url'):
                if key in saved:
                    cfg[key] = saved[key]
    return cfg

class App:
    def __init__(self, root, cfg):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
        self.root, self.cfg = root, copy.deepcopy(cfg)
        self.q, self.busy, self.current_result = queue.Queue(), False, None
        self.repair_context = None
        self.core = CodeStudioCore(ROOT, self.cfg, log=lambda s: self.q.put(('log', s)))
        self.workspace_var = tk.StringVar(value=str(self.core.workspace))
        self.model_var = tk.StringVar(value=cfg.get('selected_model', cfg['roles']['coder']['model']))
        self.status_var = tk.StringVar(value='Workspace und Modell wählen')
        self.tests_var = tk.StringVar(value=self.test_label())
        root.title('CodeStudio · Projekte mit lokaler KI bearbeiten')
        root.geometry('1180x820')
        root.minsize(900, 650)
        top = ttk.Frame(root, padding=12); top.pack(fill='x')
        ttk.Label(top, text='Projektordner').grid(row=0, column=0, sticky='w')
        self.workspace_entry = ttk.Entry(top, textvariable=self.workspace_var)
        self.workspace_entry.grid(row=0, column=1, sticky='ew', padx=8)
        self.choose_btn = ttk.Button(top, text='Ordner wählen', command=self.choose_workspace)
        self.choose_btn.grid(row=0, column=2)
        ttk.Label(top, text='Ollama-Modell').grid(row=1, column=0, sticky='w', pady=8)
        self.model_box = ttk.Combobox(top, textvariable=self.model_var)
        self.model_box.grid(row=1, column=1, sticky='ew', padx=8)
        self.models_btn = ttk.Button(top, text='Modelle laden', command=self.refresh_models)
        self.models_btn.grid(row=1, column=2)
        ttk.Label(top, textvariable=self.tests_var).grid(row=2, column=1, sticky='w', padx=8)
        self.tests_btn = ttk.Button(top, text='Tests einstellen', command=self.configure_tests)
        self.tests_btn.grid(row=2, column=2)
        top.columnconfigure(1, weight=1)
        tf = ttk.LabelFrame(root, text='Was soll geändert werden?', padding=8); tf.pack(fill='x', padx=12)
        self.task = ScrolledText(tf, height=4, wrap='word'); self.task.pack(fill='x')
        buttons = ttk.Frame(root, padding=12); buttons.pack(fill='x')
        self.analyze_btn = ttk.Button(buttons, text='Plan und Diff erzeugen', command=self.analyze); self.analyze_btn.pack(side='left')
        self.apply_btn = ttk.Button(buttons, text='Geprüften Diff anwenden', command=self.apply, state='disabled'); self.apply_btn.pack(side='left', padx=8)
        self.reject_btn = ttk.Button(buttons, text='Verwerfen', command=self.reject); self.reject_btn.pack(side='left')
        self.repair_btn = ttk.Button(buttons, text='Fehler neu planen', command=self.repair, state='disabled'); self.repair_btn.pack(side='left', padx=8)
        ttk.Label(buttons, textvariable=self.status_var).pack(side='right')
        panes = ttk.Panedwindow(root, orient='vertical'); panes.pack(fill='both', expand=True, padx=12, pady=(0,12))
        upper = ttk.Panedwindow(panes, orient='horizontal'); panes.add(upper, weight=3)
        pf = ttk.LabelFrame(upper, text='Plan und Akzeptanz'); upper.add(pf, weight=1)
        self.plan_text = ScrolledText(pf, wrap='word'); self.plan_text.pack(fill='both', expand=True)
        df = ttk.LabelFrame(upper, text='Änderungsvorschau'); upper.add(df, weight=2)
        self.diff_text = ScrolledText(df, wrap='none', font=('Consolas',10)); self.diff_text.pack(fill='both', expand=True)
        self.diff_text.configure(state='disabled')
        lf = ttk.LabelFrame(panes, text='Ablauf und Ergebnis'); panes.add(lf, weight=1)
        self.log_text = ScrolledText(lf, height=8, wrap='word'); self.log_text.pack(fill='both', expand=True)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(100, self.poll)
        self.refresh_models()

    def test_label(self):
        return f"{len(self.cfg.get('tests', []))} Testprofil(e)" if self.cfg.get('tests') else 'Keine Projekttests konfiguriert'

    def save(self):
        saved = {k:self.cfg[k] for k in ('tests','ollama_url') if k in self.cfg}
        saved.update(workspace=self.workspace_var.get(), selected_model=self.model_var.get())
        atomic_bytes(pathlib.Path(self.cfg['state_dir']) / 'preferences.json', canonical(saved))

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.workspace_entry,self.choose_btn,self.model_box,self.models_btn,self.tests_btn,self.analyze_btn,self.reject_btn):
            widget.configure(state='disabled' if busy else 'normal')
        self.apply_btn.configure(state='normal' if not busy and self.current_result else 'disabled')
        self.repair_btn.configure(state='normal' if not busy and self.repair_context else 'disabled')

    def repair(self):
        if self.busy or not self.repair_context: return
        self.task.delete('1.0','end')
        self.task.insert('end',self.repair_context)
        self.repair_context = None
        self.analyze()

    def reject(self):
        if self.busy: return
        self.core.pending.clear()
        self.current_result = None
        self.apply_btn.configure(state='disabled')
        self.diff_text.configure(state='normal')
        self.diff_text.delete('1.0','end')
        self.diff_text.configure(state='disabled')
        self.status_var.set('Vorschlag verworfen')

    def choose_workspace(self):
        from tkinter import filedialog
        selected = filedialog.askdirectory(initialdir=self.workspace_var.get())
        if selected:
            self.reject(); self.workspace_var.set(selected); self.save()

    def configure_tests(self):
        from tkinter import simpledialog, messagebox
        value = simpledialog.askstring('Projekttests', 'Argumentliste des Testprogramms (JSON).\nBeispiel: ["python", "-m", "unittest", "discover", "-v"]\n[] deaktiviert Projekttests.',
                                     initialvalue=json.dumps(self.cfg.get('tests', [{}])[0].get('argv', []) if self.cfg.get('tests') else []), parent=self.root)
        if value is None: return
        try:
            argv = json.loads(value)
            if not isinstance(argv,list) or not all(isinstance(x,str) and x for x in argv): raise ValueError('Eine Liste aus Textargumenten ist erforderlich')
            self.cfg['tests'] = [{'name':'Projekttests','argv':argv,'timeout_sec':300}] if argv else []
            self.core.config['tests'] = copy.deepcopy(self.cfg['tests'])
            self.tests_var.set(self.test_label()); self.reject(); self.save()
        except ValueError as exc: messagebox.showerror('Testkonfiguration',str(exc),parent=self.root)

    def refresh_models(self):
        if self.busy: return
        def worker():
            try: self.q.put(('models', self.core.installed_models()))
            except Exception as exc: self.q.put(('log',redact(str(exc))))
        threading.Thread(target=worker,daemon=True).start()

    def analyze(self):
        from tkinter import messagebox
        if self.busy: return
        task, model = self.task.get('1.0','end').strip(), self.model_var.get().strip()
        try:
            if not task or not model: raise ValueError('Bitte Aufgabe und Modell angeben')
            self.core.set_workspace(self.workspace_var.get())
            self.save()
        except Exception as exc:
            messagebox.showerror('CodeStudio',str(exc),parent=self.root); return
        self.current_result = None; self.set_busy(True)
        self.status_var.set('Scout → Planer → Coder → Reviewer')
        def worker():
            try: self.q.put(('analysis',self.core.analyze(task,model)))
            except Exception as exc: self.q.put(('error',redact(str(exc))))
        threading.Thread(target=worker,daemon=True).start()

    def apply(self):
        from tkinter import messagebox
        if self.busy or not self.current_result: return
        if self.workspace_var.get() != str(self.core.workspace):
            self.reject(); messagebox.showwarning('Workspace geändert','Bitte erneut analysieren.',parent=self.root); return
        note = '\nKeine Projekttests konfiguriert: Das Ergebnis bleibt ungeprüft.' if not self.cfg.get('tests') else '\nDie konfigurierten Projekttests laufen anschließend.'
        if not messagebox.askyesno('Diff anwenden',f'{len(self.current_result.final_state)} Datei(en) im angezeigten Projekt ändern?'+note,parent=self.root): return
        result = self.current_result
        self.current_result = None; self.set_busy(True); self.status_var.set('Anwenden und prüfen …')
        def worker():
            try: self.q.put(('applied',self.core.apply(result)))
            except Exception as exc: self.q.put(('error',redact(str(exc))))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        from tkinter import messagebox
        try:
            while True:
                kind,payload = self.q.get_nowait()
                if kind == 'models':
                    self.model_box.configure(values=payload)
                    if payload and self.model_var.get() not in payload: self.model_var.set(payload[0])
                elif kind == 'analysis':
                    self.current_result = payload
                    self.plan_text.delete('1.0','end'); self.plan_text.insert('end','\n'.join(payload.plan.get('plan',[]))+'\n\nAkzeptanz:\n'+'\n'.join(payload.plan.get('acceptance',[])))
                    self.diff_text.configure(state='normal')
                    self.diff_text.delete('1.0','end'); self.diff_text.insert('end',payload.diff)
                    self.diff_text.configure(state='disabled')
                    self.set_busy(False); self.status_var.set('Vorschlag prüfen')
                elif kind == 'applied':
                    if payload['status'] == 'rolled-back':
                        self.repair_context = payload['task']+'\n\nDer vorige Versuch wurde zurückgerollt. Behebe den folgenden Testfehler mit einem neuen, prüfbaren Diff:\n'+(payload.get('test') or {}).get('output','')
                    self.set_busy(False)
                    labels = {'applied':'Angewendet · Projekttests bestanden','applied-untested':'Angewendet · ohne Projekttest','rolled-back':'Test fehlgeschlagen · zurückgerollt'}
                    self.status_var.set(labels.get(payload['status'],payload['status']))
                    self.log_text.insert('end',self.status_var.get()+'\n'+(payload.get('test') or {}).get('output','')+'\nBeleg: '+str(self.core.runs / (payload['tag']+'.json'))+'\n')
                elif kind == 'error':
                    self.set_busy(False); self.status_var.set('Vorgang fehlgeschlagen')
                    messagebox.showerror('CodeStudio',payload,parent=self.root)
                else: self.log_text.insert('end',payload+'\n')
                self.log_text.see('end')
        except queue.Empty: pass
        self.root.after(100,self.poll)

    def close(self):
        from tkinter import messagebox
        if self.busy:
            messagebox.showinfo('Vorgang läuft','Bitte den laufenden Vorgang beenden lassen. Anwendung und Rückrollen werden nicht unterbrochen.',parent=self.root)
            return
        self.root.destroy()

def main():
    if '--serve' in sys.argv:
        sys.argv.remove('--serve')
        from service import main as serve
        return serve()
    if '--test-worker' in sys.argv:
        from processrunner import worker_main
        return worker_main()
    if not any(flag in sys.argv for flag in ('--doctor','--legacy-gui')):
        from vscode_launcher import main as launch_vscode
        return launch_vscode()
    if '--legacy-gui' in sys.argv:
        sys.argv.remove('--legacy-gui')
    ap = argparse.ArgumentParser()
    ap.add_argument('--doctor',action='store_true'); ap.add_argument('--config'); ap.add_argument('--workspace'); ap.add_argument('--state-dir')
    args = ap.parse_args()
    cfg = load_config(args.config,args.workspace,args.state_dir)
    if args.doctor:
        try:
            core = CodeStudioCore(ROOT,cfg)
            print(json.dumps({'python':sys.version.split()[0],'workspace':str(core.workspace),'models':core.installed_models(),'tests_configured':len(cfg.get('tests',[]))},ensure_ascii=False))
            return 0
        except Exception as exc:
            print(json.dumps({'status':'unavailable','error':redact(str(exc))},ensure_ascii=False)); return 1
    import tkinter as tk
    root = tk.Tk(); App(root,cfg); root.mainloop()
    return 0

if __name__ == '__main__': raise SystemExit(main())
