"""Bounded autonomous development: one project, one transaction, verified completion."""
from __future__ import annotations
import copy
import json
import os
import pathlib
import re
import threading
import time
import uuid
from urllib.parse import urlsplit
from core import CodeStudioCore, truncate, SCHEMA, ModelOutputError
from workflow import normalize_job, job_result
from runmemory import save_candidate, load_candidate, candidate_signature, record_success, run_id
from moduleflow import normalize_workflow, MAX_REFERENCES
from failureanalysis import analyze_failure, blocker_report
from plan_gate import acceptance_for_prompt, ensure_checks, lint_plan_acceptance, normalize_acceptance
from processrunner import run_command, ProcessTreeUncertain
from safety import WorkspaceTransaction, atomic_bytes, canonical, digest, identity, relative, redact, ensure_source_text, safe_path

class RunStopped(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)

class ProposalRejected(RuntimeError):
    pass

def fallback_models(value):
    if (not isinstance(value,list) or len(value)>3 or
        any(not isinstance(m,str) or not re.fullmatch(r'[^\s\x00-\x1f]{1,200}',m) for m in value) or
        len(set(value))!=len(value)):
        raise ValueError('Höchstens drei unterschiedliche lokale Ersatzmodelle angeben.')
    return value[:]

class AutonomousCore(CodeStudioCore):
    def checkpoint(self):
        if self.cancel_event.is_set():
            raise RunStopped('cancelled', 'Autonomer Auftrag abgebrochen.')
        if time.monotonic() >= self.deadline:
            raise RunStopped('timed_out', 'Zeitbudget des autonomen Auftrags erreicht.')

    def chat(self, role, user, model):
        self.checkpoint()
        model = getattr(self, 'role_models', {}).get(role, getattr(self,'fallback_model',None) or model)
        self.active_role = role
        if self.model_calls >= self.max_model_calls:
            raise RunStopped('budget_exhausted', 'Modellaufruf-Budget erreicht.')
        self.model_calls += 1
        if self.transport is not None:
            self.model_history.append({'call':self.model_calls,'role':role,'model':model,'provider':'host'})
        remaining = max(1, self.deadline-time.monotonic())
        self.config['timeout_sec'] = min(self.config.get('timeout_sec', 900), remaining)
        if self.transport is not None and hasattr(self.transport, 'request_timeout'):
            self.transport.request_timeout = remaining
        if role == 'coder':
            user += '\nEDIT-FORMAT: Für vollständig gelesene kleine bestehende Dateien op=write verwenden, content enthält den vollständigen neuen Dateiinhalt. op=create nur für neue Dateien. Niemals replace mit leerem old_text. Alle übrigen Dateiinhalte erhalten.'
        self._request_count = 0
        done = threading.Event()
        outcome = {}
        def request():
            try: outcome['value'] = super(AutonomousCore,self).chat(role,user,model)
            except Exception as exc: outcome['error'] = exc
            finally: done.set()
        # Model transport has no filesystem effects. Cancellation never waits on inference.
        thread = threading.Thread(target=request,daemon=True)
        thread.start()
        while not done.wait(.05): self.checkpoint()
        self.checkpoint()
        if 'error' in outcome: raise outcome['error']
        return outcome['value']

    def ollama_request(self, path, body=None, timeout=None):
        self.checkpoint()
        if path == '/api/chat':
            if body and body.get('format') == SCHEMA['coder'] and all(h is None or p in self.fully_read for p,h in self.read_identity.items()):
                body = copy.deepcopy(body)
                body['format']['properties']['edits']['items']['properties']['op']['enum']=['write','create','delete']
            self._request_count += 1
            if self._request_count > 1:
                if self.model_calls >= self.max_model_calls:
                    raise RunStopped('budget_exhausted','Modellaufruf-Budget erreicht.')
                self.model_calls += 1
            self.model_history.append({'call':self.model_calls,'role':getattr(self,'active_role',None),
                                       'model':body.get('model'),'provider':self.config.get('ollama_url')})
        return super().ollama_request(path,body,timeout=min(timeout or self.config.get('timeout_sec',900),max(.1,self.deadline-time.monotonic())))

    def pending_dependency(self, outcome):
        if outcome.get('status') != 'failed' or outcome.get('returncode') in (None,0): return None
        output = outcome.get('output','')
        if 'AssertionError' in output: return None
        patterns = [r"Error: ENOENT: no such file or directory, open '([^'\r\n]+)'",
                    r"FileNotFoundError: \[Errno 2\] No such file or directory: '([^'\r\n]+)'"]
        for pattern in patterns:
            match = re.search(pattern,output)
            if not match: continue
            try:
                p=pathlib.Path(match.group(1))
                p=p if p.is_absolute() else self.workspace/p
                rel=p.resolve().relative_to(self.workspace).as_posix()
                owner=getattr(self,'future_artifacts',{}).get(rel)
                if owner and not safe_path(self.workspace,rel,True).exists():
                    return {'path':rel,'producer_module':owner}
            except (ValueError,OSError): pass
        return None

    def run_tests(self):
        self.checkpoint()
        if not self.config['tests']:
            return {'status':'deferred', 'returncode':None, 'results':[],
                    'output':'Noch kein unabhängiger Test ausführbar. Modul nur quelltextgeprüft; Gesamttests bleiben verpflichtend.'}
        results = []
        pending = False
        if not hasattr(self,'deferred_profiles'): self.deferred_profiles={}
        for profile in self.config['tests']:
            self.checkpoint()
            key=digest(canonical(profile))
            cached=self.deferred_profiles.get(key)
            if cached and cached['dependency']['path'] in getattr(self,'future_artifacts',{}) and not safe_path(self.workspace,cached['dependency']['path'],True).exists():
                results.append({**cached,'execution':'not_repeated_until_dependency_exists'});pending=True;continue
            self.deferred_profiles.pop(key,None)
            self.log('Autonom: Projekttest '+str(profile.get('name') or profile['argv'][0]))
            timeout = min(profile.get('timeout_sec', 300), max(.1, self.deadline-time.monotonic()))
            outcome = run_command(profile['argv'], self.workspace, timeout, cancel_event=self.cancel_event)
            if outcome['returncode']==0 and re.search(r'\bRan 0 tests?\b|(?:^|\n)\s*(?:#|ℹ)?\s*tests 0\s*(?:\n|$)',outcome['output']):
                outcome={**outcome,'returncode':126,'status':'no_tests','output':outcome['output']+'\nKeine Tests ausgeführt; kein autonomer Abschluss.'}
            row = {**outcome, 'name':profile.get('name') or profile['argv'][0], 'profile_id':key,
                   'output':truncate(redact(outcome['output']), 12000)}
            dependency=self.pending_dependency(outcome)
            if dependency:
                row={**row,'status':'pending_dependency','dependency':dependency}
                self.deferred_profiles[key]=row
                results.append(row);pending=True;continue
            results.append(row)
            if outcome['returncode'] != 0:
                return {**row, 'results':results}
        if pending:
            return {'status':'deferred','returncode':None,'results':results,
                    'output':'Geplante spätere Dateien fehlen; betroffene Tests bleiben ungeprüft.\n'+'\n'.join(x['output'] for x in results)}
        return {'status':'passed', 'returncode':0, 'results':results,
                'output':'\n'.join(x['output'] for x in results)}

def is_test_file(rel):
    p = pathlib.PurePosixPath(rel)
    return any(x.lower() in ('test','tests','__tests__') for x in p.parts) or bool(
        re.search(r'(^test_|_test\.|\.test\.|\.spec\.)', p.name, re.I))

class AutonomousRun:
    """Only caller-approved test argv run; models never choose shell commands."""
    def __init__(self, root, config, request, emit=lambda _:None, transport=None):
        self.request = copy.deepcopy(request)
        self.id = request.get('run_id')
        if not isinstance(self.id,str) or not re.fullmatch(r'[a-f0-9]{32}',self.id):
            raise ValueError('Gültige einmalige Auftrags-ID erforderlich.')
        if request.get('approved') is not True:
            raise ValueError('Autonomen Auftrag für dieses Projekt zuerst starten.')
        self.job = normalize_job(request['job']) if 'job' in request else None
        self.task = (self.job['task']+'\nAKZEPTANZ:\n'+acceptance_for_prompt(self.job.get('acceptance'))) if self.job else request.get('task')
        self.model = request.get('model')
        if not isinstance(self.task,str) or not self.task.strip() or len(self.task)>24000:
            raise ValueError('Aufgabe mit Akzeptanzkriterien erforderlich.')
        ensure_source_text(self.task)
        if not isinstance(self.model,str) or not self.model.strip():
            raise ValueError('Installiertes Modell erforderlich.')
        limits = request.get('limits', {})
        if not isinstance(limits,dict): raise ValueError('Ungültige Auftragsgrenzen.')
        self.limits = {}
        for key, default, low, high in [('steps',6,1,12),('repairs',3,0,8),('seconds',1800,10,7200),('model_calls',80,4,200)]:
            n = limits.get(key,default)
            if type(n) is not int or not low<=n<=high: raise ValueError('Ungültiges Budget: '+key)
            self.limits[key] = n
        tests = list(config.get('tests',[]) or [])
        # SoftKI: leere config.tests erlaubt; ensure_checks bindet Plan-Checks vor dem Coder.
        if 'workflow' in request and not tests:
            raise ValueError('Expliziter Workflow benötigt konfigurierte Projekttests.')
        for t in tests:
            if not isinstance(t,dict) or not isinstance(t.get('argv'),list) or not t['argv'] or not all(isinstance(a,str) and a for a in t['argv']):
                raise ValueError('Testprofil benötigt Programm und Argumente.')
            if pathlib.Path(t['argv'][0]).suffix.lower() in ('.bat','.cmd'):
                raise ValueError('Direktes Testprogramm statt Batch-Datei einstellen.')
            if type(t.get('timeout_sec',300)) is not int or not 1<=t.get('timeout_sec',300)<=3600:
                raise ValueError('Ungültiger Test-Timeout.')
        self.workflow = normalize_workflow(request['workflow'], len(tests), self.limits['steps']) if 'workflow' in request else None
        self.planning = request.get('planning', 'modules')
        if self.planning not in ('modules', 'steps'): raise ValueError('Unbekannter Planungsmodus.')
        self.all_tests = copy.deepcopy(tests)
        self.module = None
        self.emit = emit
        self.cancel_event = threading.Event()
        self.core = AutonomousCore(root, config, log=lambda msg:emit({'event':'log','text':redact(msg)}), transport=transport)
        # The outer workflow owns coder and malformed-output retries.
        self.core.single_attempt = True
        self.core.module_mode = bool(self.workflow)
        self.core.cancel_event = self.cancel_event
        self.core.deadline = time.monotonic()+self.limits['seconds']
        self.core.model_calls = 0
        self.core.max_model_calls = self.limits['model_calls']
        self.core.model_history = []
        self.fallbacks = fallback_models(config.get('autonomous_fallback_models',[]))
        if self.fallbacks:
            endpoint=urlsplit(config.get('ollama_url',''))
            if (transport is not None or endpoint.scheme!='http' or endpoint.hostname not in ('localhost','127.0.0.1','::1')
                or endpoint.username or endpoint.password or endpoint.path not in ('','/') or endpoint.query or endpoint.fragment):
                raise ValueError('Ersatzmodelle gelten nur für denselben lokalen Standalone-Dienst.')
        self.fallback_index = 0
        self.path = self.core.runs/('autonomous-'+self.id+'.json')
        self.lock = self.core.workspace/'.codestudio-apply.lock'
        self.tx = WorkspaceTransaction(self.core.workspace,self.core.backups,'autonomous-'+self.id)
        self.receipt = {'schema':'codestudio.autonomous.v1','run_id':self.id,'workspace':str(self.core.workspace),
                        'task':self.task,'model':self.model,'limits':self.limits,'status':'running',
                        'steps':[],'attempts':[],'started_at':time.time(),'actor':request.get('actor','user'),
                        'workflow_id':request.get('workflow_id'), 'tests_sha256':digest(canonical(tests))}
        if self.job: self.receipt['job'] = self.job
        if self.workflow: self.receipt['workflow'] = self.workflow
        self.protected = {}
        self.expected = {}
        self.latest = {}
        self.unverified_attempts = 0
        self.problem_counts = {}
        self.problem_profiles = {}
        self.failed_candidates = []
        self.original_workflow = copy.deepcopy(self.workflow)
        if request.get('resume_from'):
            run_id(request['resume_from'])
            approach=request.get('changed_approach')
            if not isinstance(approach,str) or not 12<=len(approach.strip())<=4000:
                raise ValueError('Geänderten Ansatz für die Wiederaufnahme konkret benennen.')
            ensure_source_text(approach)
            if self.workflow or self.planning!='modules' or self.job:
                raise ValueError('Wiederaufnahme verwendet den gesicherten Originalworkflow.')

    def record_failure(self, outcome, phase='implementation'):
        analysis=analyze_failure(self.core.workspace,outcome.get('output',''),
            status=outcome.get('status','failed'),profile=outcome.get('name',''),
            profile_id=outcome.get('profile_id',''),
            phase=phase,module=self.module['id'] if self.module else None,protected=self.protected)
        key=analysis['fingerprint']
        count=self.problem_counts.get(key,0)+1
        self.problem_counts[key]=count
        self.problem_profiles[key]=outcome.get('profile_id') or analysis['test_profile']
        self.receipt.setdefault('failure_analysis',[]).append({**analysis,'attempts':count})
        self.last_failure_analysis=analysis
        self.save()
        if count>=4:
            self.receipt['blocker_report']=blocker_report(analysis,count)
            self.final_feedback=None
            raise RunStopped('stalled',self.receipt['blocker_report']['summary'])
        return analysis

    def record_test_progress(self, outcome):
        passed={row.get('profile_id') or row.get('name','') for row in outcome.get('results',[]) if row.get('returncode')==0}
        for key,profile in list(self.problem_profiles.items()):
            if profile and profile in passed:
                self.problem_counts.pop(key,None);self.problem_profiles.pop(key,None)


    @staticmethod
    def sanitize_module_test_indices(plan, test_count):
        """Drop out-of-range / non-int module test indices before director validate.

        Empty lists stay empty (pending OK with allow_pending_tests). Mutates plan modules in place.
        """
        if not isinstance(plan, dict):
            return plan
        n = int(test_count) if type(test_count) is int else 0
        modules = plan.get('modules')
        if not isinstance(modules, list):
            return plan
        for m in modules:
            if not isinstance(m, dict):
                continue
            tests = m.get('tests')
            if isinstance(tests, list):
                m['tests'] = [i for i in tests if type(i) is int and 0 <= i < n]
        return plan

    def bind_plan_checks(self, plan):
        """Lint acceptance and bind executable checks before any coder write."""
        if not isinstance(plan, dict):
            raise ValueError('Plan für SoftKI-Checks erforderlich.')
        lint_plan_acceptance(plan)
        profiles = ensure_checks(self.core.workspace, plan, existing_tests=self.all_tests)
        if not profiles:
            raise ValueError('SoftKI: Ausführbare acceptance.checks oder config.tests vor dem Coder erforderlich.')
        self.all_tests = copy.deepcopy(profiles)
        self.core.config['tests'] = copy.deepcopy(profiles)
        self.receipt['tests_sha256'] = digest(canonical(profiles))
        self.receipt['plan_acceptance'] = normalize_acceptance(plan.get('acceptance'))
        self.save()
        return profiles

    def numeric_failure_feedback(self, analysis=None):
        """Repair hint from programmatic analyze_failure — keep numeric evidence."""
        a = analysis or getattr(self, 'last_failure_analysis', None)
        if not a:
            return ''
        parts = []
        comp = a.get('comparison') or {}
        if comp:
            parts.append('NUMERISCH: ' + json.dumps(comp, ensure_ascii=False))
        locs = a.get('locations') or []
        if locs:
            parts.append('STELLEN: ' + json.dumps(locs, ensure_ascii=False))
        known = a.get('known') or ''
        if known:
            parts.append('BELEG: ' + known[:800])
        excerpts = a.get('source_excerpts') or []
        if excerpts:
            parts.append('QUELLTEXT: ' + json.dumps(excerpts[:2], ensure_ascii=False)[:1200])
        return '\n'.join(parts)

    def save(self):
        self.receipt['model_calls'] = self.core.model_calls
        self.receipt['model_history'] = list(self.core.model_history)
        self.receipt['updated_at'] = time.time()
        atomic_bytes(self.path,canonical(self.receipt))
        self.emit({'event':'autonomous','run_id':self.id,'status':self.receipt['status'],
                   'completed_steps':len(self.receipt['steps']),'receipt_path':str(self.path)})

    def unchanged(self):
        for rel, expected in {**self.expected, **self.protected}.items():
            if identity(self.core.workspace,rel) != expected:
                raise RunStopped('conflict','Datei außerhalb des Auftrags verändert: '+rel)

    def proposal(self, task):
        self.core.checkpoint()
        self.unchanged()
        try:
            result = self.core.analyze(task,self.model,self.module) if self.module else self.core.analyze(task,self.model)
        except RunStopped: raise
        except (ValueError,RuntimeError) as exc:
            self.core.checkpoint()
            self.unchanged()
            if self.core.transport: self.core.transport('models',{})
            raise ProposalRejected(redact(str(exc))) from exc
        self.core.checkpoint()
        self.unchanged()
        if self.core.transport: self.core.transport('models',{})
        self.core.checkpoint()
        if self.module and not set(result.final_state).issubset(self.module['files']):
            raise ValueError('Vorschlag außerhalb des Modulvertrags.')
        if any(rel in self.protected for rel in result.final_state):
            raise ValueError('Vorhandene Testdateien/Testprogramme dürfen nicht automatisch abgeschwächt werden.')
        if len(set(self.tx.files)|set(result.final_state)) > 32:
            raise RunStopped('budget_exhausted','Maximal 32 verschiedene Dateien pro autonomem Auftrag.')
        signature=candidate_signature(self,result.final_state)
        if signature in self.failed_candidates:
            raise ProposalRejected('Identischer bereits fehlgeschlagener Kandidat bei unveränderten Tests. Ansatz oder Eingaben wirksam ändern.')
        self.core.claim_proposal(result)
        # Freeze every read file, including new paths. Never absorb concurrent edits.
        for rel,h in result.binding['before'].items(): self.expected.setdefault(rel,h)
        self.unchanged()
        for rel,new in result.final_state.items():
            self.core.checkpoint()
            if identity(self.core.workspace,rel) != self.expected[rel]:
                raise RunStopped('conflict','Datei während Anwendung verändert: '+rel)
            if new is None: self.tx.delete(rel)
            else: self.tx.write(rel,new)
            self.expected[rel] = identity(self.core.workspace,rel)
        attempt = {'proposal_id':result.receipt['tag'],'diff':result.diff,
                   'diff_sha256':digest(result.diff.encode('utf-8')),
                   'candidate_signature':signature,
                   'files':list(result.final_state),'after':{p:self.expected[p] for p in result.final_state},
                   'analyze_receipt':{'tag':result.receipt.get('tag'),'status':result.receipt.get('status'),
                                      'plan':result.receipt.get('plan'),'diff_sha256':digest(result.diff.encode('utf-8'))},
                   'apply_receipt':{'schema':'codestudio.apply.v1','proposal_id':result.receipt['tag'],
                                    'files':list(result.final_state),
                                    'after':{p:self.expected[p] for p in result.final_state},
                                    'applied_at':time.time(),
                                    'diff_sha256':digest(result.diff.encode('utf-8'))},
                   'models':{role:getattr(self.core,'role_models',{}).get(role,getattr(self.core,'fallback_model',None) or self.model)
                             for role in ('planner','coder','reviewer')}}
        if self.module: attempt['module_id'] = self.module['id']
        self.receipt['attempts'].append(attempt)
        save_candidate(self)
        self.save()  # Durable candidate bytes before any test program starts.
        test = self.core.run_tests()
        attempt['test'] = test
        self.latest = test
        self.unchanged()
        self.core.checkpoint()
        self.save()
        self.record_test_progress(test)
        if test.get('returncode') not in (None,0):
            evidence=analyze_failure(self.core.workspace,test.get('output',''),status=test.get('status','failed'),protected=self.protected)
            if evidence['kind'] in ('assertion','syntax'):
                self.failed_candidates.append(signature)
            save_candidate(self)
            self.record_failure(test)
        return result, test

    def execute(self):
        if self.path.exists() or self.tx.root.exists():
            raise ValueError('Auftrags-ID bereits verwendet; keine erneute Ausführung.')
        fd = os.open(self.lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        uncertain = False
        try:
            os.write(fd,self.id.encode('ascii'))
            self.tx.manifest()
            tree = self.core.file_tree()
            self.protected = {p:identity(self.core.workspace,p) for p in tree if is_test_file(p)}
            for profile in self.core.config['tests']:
                for arg in profile['argv']:
                    try:
                        p=pathlib.Path(arg)
                        if p.is_absolute(): rel=p.resolve().relative_to(self.core.workspace).as_posix()
                        else: rel=relative(arg)
                        if identity(self.core.workspace,rel) is not None: self.protected[rel]=identity(self.core.workspace,rel)
                    except (ValueError,OSError): pass
            self.expected = dict(self.protected)
            self.receipt['protected_tests'] = self.protected
            self.save()
            if self.fallbacks:
                installed=set(self.core.installed_models())
                if any(m not in installed for m in self.fallbacks):
                    raise RunStopped('blocked','Konfigurierte Ersatzmodelle sind am lokalen Dienst nicht installiert.')
                # Non-thinking candidates must never inherit an incompatible thinking request.
                if self.core.config.get('think') not in (None,False):
                    for model in self.fallbacks:
                        info=self.core.ollama_request('/api/show',{'model':model})
                        if 'thinking' not in info.get('capabilities',[]):
                            raise RunStopped('blocked','Denkmodus ist mit Ersatzmodell nicht kompatibel: '+model)
            if self.request.get('resume_from'):
                self.restore_candidate()
                tree=self.core.file_tree()
            if not self.workflow and self.planning == 'modules':
                self.plan_modules(tree)
                if isinstance(self.receipt.get('plan'), dict):
                    acc = normalize_acceptance(self.receipt['plan'].get('acceptance'))
                    if acc.get('checks') or not self.all_tests:
                        self.bind_plan_checks(self.receipt['plan'])
            if self.workflow:
                if not self.all_tests:
                    seed = self.receipt.get('plan') or {'acceptance': (self.job or {}).get('acceptance')}
                    if seed:
                        self.bind_plan_checks(seed if isinstance(seed, dict) else {'acceptance': seed})
                    if not self.all_tests:
                        raise ValueError('SoftKI: Keine ausführbaren Checks vor Modul-Coder.')
                continuations=[]
                start_index=0
                for final_attempt in range(self.limits['repairs']+1):
                    self.final_feedback = None
                    try:
                        while True:
                            future=[m for flow,index,_ in reversed(continuations)
                                    for m in flow['modules'][index:]]
                            self.execute_modules(start_index=start_index, finalize=not continuations,
                                                 future_modules=future)
                            if not continuations: break
                            self.workflow,start_index,original_plan=continuations.pop()
                            self.receipt['workflow']=copy.deepcopy(self.workflow)
                            self.receipt['plan']=original_plan
                            self.receipt['workflow_sha256']=digest(canonical(self.workflow))
                            self.receipt.setdefault('resumed_workflows',[]).append({
                                'workflow_sha256':self.receipt['workflow_sha256'],
                                'module':self.workflow['modules'][start_index]['id'],
                                'remaining_modules':[m['id'] for m in self.workflow['modules'][start_index:]]})
                            self.core.log('Autonom: unterbrochenen Modulvertrag prüfen und offenen Arbeitsplan fortsetzen')
                            self.save()
                        break
                    except RunStopped:
                        if not self.final_feedback or self.request.get('workflow') or final_attempt == self.limits['repairs']:
                            raise
                        if self.failed_module_index is not None:
                            continuations.append((copy.deepcopy(self.workflow),self.failed_module_index,
                                                  copy.deepcopy(self.receipt['plan'])))
                        self.receipt.setdefault('integration_repairs', []).append({
                            'attempt':final_attempt+1, 'feedback':self.final_feedback,
                            'previous_workflow_sha256':digest(canonical(self.workflow)),
                            'continuation_modules':[[m['id'] for m in flow['modules'][index:]]
                                                    for flow,index,_ in continuations]})
                        self.core.log('Autonom: Gesamtbefund in begrenzte Reparaturmodule übersetzen')
                        self.plan_modules(self.core.file_tree(), self.final_feedback,
                                          interim_repair=bool(continuations))
                        start_index=0
            else:
                plan = self.core.chat('planner','AUTONOMER GESAMTAUFTRAG:\n'+self.task+
                    '\nZerlege in maximal '+str(self.limits['steps'])+' zusammenhängende Implementierungsschritte. '
                    'Jeder plan-Eintrag ist ein konkreter Teilauftrag. Keine Schritte nur zum Ausführen von Tests, Review oder Freigeben: das übernimmt die Laufzeit. '
                    'Keine Rückfrage außer fehlenden zwingenden Angaben.\nDATEIBAUM:\n'+'\n'.join(tree), self.model)
                if plan.get('questions'): raise RunStopped('blocked','Rückfrage: '+redact(str(plan['questions'])))
                steps = plan.get('plan')
                if not isinstance(steps,list) or not steps or len(steps)>self.limits['steps'] or not all(isinstance(s,str) and s.strip() for s in steps):
                    raise ValueError('Plan muss nichtleere Schritte innerhalb des Budgets enthalten.')
                if plan.get('acceptance') in (None, [], {}):
                    raise ValueError('Plan benötigt überprüfbare Akzeptanzkriterien.')
                normalize_acceptance(plan.get('acceptance'))  # shape check
                self.receipt['plan'] = plan
                self.save()
                self.bind_plan_checks(plan)  # SoftKI: ensure_checks vor Coder
                feedback = ''
                for number, step in enumerate(steps,1):
                    self.core.log('Autonom: Schritt '+str(number)+'/'+str(len(steps))+': '+step)
                    subtask = self.task+'\n\nJETZT NUR DIESEN TEILSCHRITT IMPLEMENTIEREN: '+step+'\nBereits erledigt: '+json.dumps(self.receipt['steps'],ensure_ascii=False)
                    try:
                        result,test = self.proposal(subtask)
                        self.receipt['steps'].append({'number':number,'task':step,'proposal_id':result.receipt['tag'],'status':'implemented'})
                    except ProposalRejected as exc:
                        self.receipt['steps'].append({'number':number,'task':step,'status':'deferred_to_qc','error':str(exc)})
                        self.core.log('Teilschritt wird durch Gesamtprüfung/Reparatur geklärt: '+str(exc))
                        self.record_failure({'output':str(exc),'status':'failed'},phase='proposal')
                    self.save()
                if not self.latest:
                    self.latest = self.core.run_tests()
                    self.record_test_progress(self.latest)
                    if self.latest.get('returncode') not in (None,0): self.record_failure(self.latest,phase='final_testing')
                for repair in range(self.limits['repairs']+1):
                    self.core.checkpoint()
                    self.unchanged()
                    if self.latest.get('returncode') == 0:
                        context = self.core.read_files(list(self.tx.files))
                        review = self.core.chat('reviewer','Gesamtergebnis gegen Originalauftrag prüfen. Verbleibende Fehler/Vollständigkeitslücken benennen.\nAUFTRAG:\n'+self.task+
                            '\nAKZEPTANZ:\n'+acceptance_for_prompt(plan.get('acceptance'))+
                            '\nAKTUELLE DATEIEN:\n'+json.dumps(context,ensure_ascii=False)+
                            '\nTESTERGEBNIS:\n'+self.latest['output'],self.model)
                        self.receipt['final_review'] = review
                        if review.get('verdict')=='ok':
                            self.unchanged()
                            self.core.checkpoint()
                            self.receipt['status']='succeeded'
                            break
                        feedback = redact(json.dumps(review,ensure_ascii=False))
                        self.record_failure({'output':json.dumps(review.get('issues',[])), 'status':'failed'},phase='final_review')
                    else:
                        num=self.numeric_failure_feedback(); feedback = 'Projekttest fehlgeschlagen. Aktuell fehlerhafter Code ist noch im Workspace. Repariere ihn, vorhandene Tests nicht ändern.\n'+self.latest.get('output','')+('\n'+num if num else '')
                    if repair == self.limits['repairs']:
                        raise RunStopped('budget_exhausted','Reparaturbudget erreicht; Ziel noch nicht verifiziert.')
                    self.core.log('Autonom: Reparatur '+str(repair+1)+'/'+str(self.limits['repairs']))
                    try:
                        self.proposal(self.task+'\n\nAUTOMATISCHE REPARATUR:\n'+feedback+'\nBisherige Schritte: '+json.dumps(self.receipt['steps'],ensure_ascii=False))
                    except ProposalRejected as exc:
                        self.receipt.setdefault('repair_errors',[]).append(str(exc))
                        self.core.log('Reparaturvorschlag ungültig: '+str(exc))
                        self.record_failure({'output':str(exc),'status':'failed'},phase='proposal')
                        self.save()
            record_success(self)
            self.receipt['after'] = {p:identity(self.core.workspace,p) for p in self.tx.files}
            self.receipt['test'] = self.latest
            self.receipt['finished_at'] = time.time()
            self.save()
            atomic_bytes(self.tx.root/'effect-receipt.json',canonical(self.receipt))
        except Exception as exc:
            self.receipt['error'] = redact(str(exc))
            self.receipt['status'] = exc.status if isinstance(exc,RunStopped) else 'failed'
            self.receipt['test'] = self.latest
            if self.receipt['status']!='cancelled' and 'blocker_report' not in self.receipt:
                timed=self.receipt['status']=='timed_out'
                analysis=analyze_failure(self.core.workspace,str(exc),
                    status='timed_out' if timed else self.receipt['status'],
                    phase=getattr(self.core,'active_role','execution'),
                    module=self.module['id'] if self.module else None,protected=self.protected)
                self.receipt['blocker_report']=blocker_report(analysis,self.problem_counts.get(analysis['fingerprint'],1))
            if 'blocker_report' in self.receipt:
                self.receipt['blocker_report']['stop_reason']=redact(str(exc))
                if self.latest.get('returncode') not in (None,0):
                    self.receipt['blocker_report']['last_test_failure']=analyze_failure(
                        self.core.workspace,self.latest.get('output',''),status=self.latest.get('status','failed'),
                        profile=self.latest.get('name',''),profile_id=self.latest.get('profile_id',''),protected=self.protected)
            if self.latest.get('returncode') not in (None,0):
                evidence=analyze_failure(self.core.workspace,self.latest.get('output',''),status=self.latest.get('status','failed'),protected=self.protected)
                if evidence['kind'] in ('assertion','syntax'):
                    self.failed_candidates.append(candidate_signature(self,{}))
            if 'experience' in self.receipt:
                self.receipt['experience']['status']='invalidated_by_failed_delivery'
                atomic_bytes(self.core.runs/('experience-'+self.id+'.json'),canonical(self.receipt['experience']))
            try:
                save_candidate(self)
            except Exception as memory_error:
                self.receipt['candidate_error']=redact(str(memory_error))
            if isinstance(exc,ProcessTreeUncertain):
                uncertain = True
                self.receipt['status']='recovery_required'
            else:
                try:
                    self.receipt['rollback'] = self.tx.rollback()
                    self.receipt['rollback_verified']=True
                except Exception as rollback_error:
                    self.receipt['status']='rollback_conflict'
                    self.receipt['rollback_error']=redact(str(rollback_error))
            self.receipt['after'] = {p:identity(self.core.workspace,p) for p in self.tx.files}
        finally:
            self.core.pending.clear()
            # Cancellation/failure may occur between progress saves.
            self.receipt['model_calls'] = self.core.model_calls
            self.receipt['model_history'] = list(self.core.model_history)
            self.receipt['finished_at']=time.time()
            self.receipt['updated_at']=self.receipt['finished_at']
            if 'blocker_report' in self.receipt:
                self.receipt['blocker_report']['rollback_verified']=self.receipt.get('rollback_verified',False)
            errors=[]
            for target in (self.path,self.tx.root/'effect-receipt.json'):
                try: atomic_bytes(target,canonical(self.receipt))
                except OSError as exc: errors.append(redact(str(exc)))
            os.close(fd)
            if errors:
                self.receipt['status']='recovery_required'
                self.receipt['persistence_errors']=errors
                # Keep ownership marker so another run cannot overwrite uncertain evidence.
            elif not uncertain: self.lock.unlink()
            self.emit({'event':'autonomous','run_id':self.id,'status':self.receipt['status'],
                       'completed_steps':len(self.receipt['steps']),'receipt_path':str(self.path)})
        result = {'receipt':self.receipt,'receipt_path':str(self.path)}
        if self.job: result['workflow_result'] = job_result(self.job,self.receipt,str(self.path))
        return result

    def restore_candidate(self):
        p=load_candidate(self.core.runs,self.request['resume_from'],self.core.workspace,self.all_tests,self.limits['steps'])
        if self.task!=p['task']: raise ValueError('Originalauftrag bei Wiederaufnahme verändert.')
        if self.protected!=p['protected']: raise ValueError('Prüfdateien bei Wiederaufnahme verändert.')
        self.workflow=normalize_workflow(p['workflow'],len(self.all_tests),self.limits['steps'],allow_pending_tests=True)
        self.original_workflow=copy.deepcopy(self.workflow)
        self.original_plan=copy.deepcopy(p.get('plan'))
        self.receipt['plan']=self.original_plan or {'workflow':self.workflow,'origin':'validated_snapshot'}
        self.authorized_files={f for m in self.workflow['modules'] for f in m['files']}
        self.review_paths={f for m in self.workflow['modules'] for f in m['files']+m['references']}
        self.expected=dict(p['basis'])
        self.unchanged()
        # Fresh transaction owns every restored byte; historical tests grant no pass.
        for rel,entry in p['files'].items():
            self.core.checkpoint();self.unchanged()
            if entry['content'] is None:
                if identity(self.core.workspace,rel) is not None: self.tx.delete(rel)
            else: self.tx.write(rel,entry['content'])
            self.expected[rel]=entry['after']
        self.failed_candidates=list(p.get('failed_candidates',[]))
        self.resume_evidence=p.get('failure_analysis',[])[-4:]
        self.core.module_mode=True
        self.receipt['workflow']=copy.deepcopy(self.workflow)
        self.receipt['workflow_sha256']=digest(canonical(self.workflow))
        self.receipt['planning_origin']='validated_original_workflow_resume'
        self.receipt['resume']={'source_run':p['run_id'],'changed_approach':self.request['changed_approach'],
            'basis_verified':True,'historical_steps_accepted':False,'source_model':p['model'],
            'current_model':self.model,'source_options':p['options'],'current_options':self.core.config.get('options',{})}
        # Older snapshots may carry a plan that never covered an explicit output.
        from director import required_artifacts, missing_artifacts
        docs={rel:self.core.read_reference(rel)[0] for rel in p['basis']
              if pathlib.PurePosixPath(rel).name.lower() in ('readme.md','requirements.md','spec.md')
              and identity(self.core.workspace,rel) is not None}
        required=required_artifacts(docs)
        missing=missing_artifacts(self.workflow,required,self.core.file_tree())
        if missing:
            self.receipt['resume']['replanned_missing_artifacts']=missing
            self.core.log('Autonom: gesicherter Plan enthält keine Erzeuger für '+', '.join(missing)+'; vollständig neu planen')
            if p.get('_planning_candidates'):
                self.recovered_plan=p['_planning_candidates'][-1]
            self.plan_modules(self.core.file_tree())
        save_candidate(self);self.save()

    def plan_modules(self, tree, repair_feedback=None, *, interim_repair=False):
        """Generate and validate contracts before any model-directed project write."""
        from director import validate_director, required_artifacts
        self.core.checkpoint(); self.unchanged()
        preferred = [p for p in tree if pathlib.PurePosixPath(p).name.lower() in
                     ('readme.md', 'requirements.md', 'spec.md', 'agents.md')]
        entries = []
        for profile in self.all_tests:
            for arg in profile['argv']:
                try:
                    p = pathlib.Path(arg)
                    rel = p.resolve().relative_to(self.core.workspace).as_posix() if p.is_absolute() else relative(arg)
                    if rel in tree: entries.append(rel)
                except (ValueError, OSError): pass
        candidates = list(dict.fromkeys(preferred + entries +
                     [hit['path'] for hit in self.core.scout(self.task, tree)] + list(self.protected)))
        mandatory = preferred[:]
        if repair_feedback:
            mandatory = list(dict.fromkeys(preferred + sorted(self.authorized_files)))
            candidates = list(dict.fromkeys(mandatory + sorted(self.review_paths) + candidates))
        # Protection covers the entire test suite; model context selects complete relevant files.
        # Never silently clip a selected file or confuse omitted tests with writable files.
        max_files = min(24, int(self.core.config.get('context_max_files', 40)))
        paths = []; total = 0; omitted = []
        per_file = int(self.core.config.get('context_max_bytes_per_file',20000))
        for rel in candidates:
            if safe_path(self.core.workspace,rel,True).exists():
                text, binary = self.core.read_reference(rel)
            else:
                text = '<neu – existiert nicht>'
            size = len(text.encode('utf8'))
            fits = len(paths)<max_files and size<=per_file and total+size<=60000
            if not fits:
                if rel in mandatory: raise RunStopped('blocked','Vollständiger Pflichtkontext überschreitet das Planbudget: '+rel)
                omitted.append(rel); continue
            paths.append(rel); total += size
        context = self.core.read_files(paths, readonly_assets=True)
        if self.core.truncated or len(context) != len(paths):
            raise RunStopped('blocked', 'Planungskontext unvollständig.')
        self.receipt['planning_context_omitted'] = omitted
        self.unchanged()
        self.expected.update(self.core.read_identity)
        self.receipt['planning_context'] = dict(self.core.read_identity)
        required_files=required_artifacts({p:t for p,t in context.items() if p in preferred})
        self.receipt['required_artifacts']=required_files
        prompt = ('ORIGINAL TASK:\n'+self.task+'\nMAXIMUM MODULES: '+str(self.limits['steps'])+
                  '\nAVAILABLE TEST PROFILES (indices are the only permitted test selections):\n'+
                  json.dumps([{'index':i, **p} for i,p in enumerate(self.all_tests)], ensure_ascii=False)+
                  '\nOnly use test indices from the AVAILABLE TEST PROFILES list.\nFILE TREE:\n'+'\n'.join(tree)+'\nOMITTED FROM PLANNING CONTEXT (still protected; reference relevant files in module references):\n'+json.dumps(omitted)+'\nREAD-ONLY PROJECT CONTRACTS:\n'+
                  '\n\n'.join('FILE '+p+'\n'+t for p,t in context.items()))
        if not self.all_tests:
            prompt += ('\nSOFTKI: No prefilled config.tests. Return acceptance as object with checks '
                       '(each check: name + argv string list OR path) and prose. '
                       'Path checks may name files the run will create; only existing tests/scripts are executed immediately. '
                       'Quantified prose without checks is rejected. Module test indices refer to those checks once bound. '
                       'On Windows, acceptance.checks must use path or python argv, not Unix test/grep. Put unittest/pytest in acceptance.checks as argv, not only in prose. Only use test indices from the AVAILABLE TEST PROFILES list.')
        prompt+='\nEXPLICIT FILE-CONTRACT OUTPUTS (every absent file needs a producing module):\n'+json.dumps(required_files)
        if self.request.get('resume_from') and not repair_feedback:
            prompt+='\nRESTORED CANDIDATE FILES (include every file in a module; unchanged files may be revalidated without edits):\n'+json.dumps(sorted(self.tx.files))
        if getattr(self,'resume_evidence',None):prompt+='\nPRIOR OBSERVED FAILURES (repair these within the original task):\n'+json.dumps(self.resume_evidence)
        if repair_feedback:
            prompt += ('\nCURRENT TEST DEFECTS:\n'+repair_feedback+
                       '\nPlan only repairs of these defects in these already authorized files: '+
                       json.dumps(sorted(self.authorized_files))+'. Preserve working behavior. Do not repeat completed implementation.')
            if interim_repair:
                prompt += ('\nThis is an INTERIM REPAIR, not final integration. The runtime retains '
                           'the interrupted module and all unstarted original modules, and resumes them '
                           'after this repair. Missing future implementation is still pending work, '
                           'not evidence that other modules are correct. Select only tests suitable '
                           'for the repaired files at this stage. Do not rebuild the remaining workflow.')
        feedback = ''
        invalid_plans = 0
        clarification_refined = False
        self.core.director_mode = True
        try:
            for attempt in range(self.limits['repairs']+1):
                self.unchanged(); self.core.checkpoint()
                self.core.log('Autonom: Arbeitsplan aus Auftrag und Projektverträgen erstellen')
                value = None
                try:
                    if attempt==0 and getattr(self,'recovered_plan',None) is not None:
                        value=self.recovered_plan;self.recovered_plan=None
                        self.receipt['resume']['reused_plan_candidate']=True
                        self.core.log('Autonom: vollständig gespeicherten Planvorschlag gegen die korrigierten Regeln erneut prüfen')
                    else:
                        value = self.core.chat('planner', prompt+feedback, self.model)
                    self.unchanged()
                    if isinstance(value, dict) and (value.get('acceptance') is not None or not self.all_tests):
                        # SoftKI: bind executable checks before director validation uses indices.
                        if value.get('acceptance') is not None or value.get('checks') is not None:
                            seed = value if 'acceptance' in value else {'acceptance': {'checks': value.get('checks', []), 'prose': value.get('prose', [])}}
                            self.bind_plan_checks(seed)
                    # SoftKI: planner may cite stale/out-of-range indices after bind shrinks profiles.
                    if isinstance(value, dict):
                        self.sanitize_module_test_indices(value, len(self.all_tests))
                    workflow = validate_director(value, len(self.all_tests), self.limits['steps'], self.protected, tree,
                                                 final_tests=not interim_repair, required_files=() if repair_feedback else required_files,
                                                 retained_files=self.tx.files if self.request.get('resume_from') and not repair_feedback else ())
                    # Requirements and selected executable test contracts must reach the coder
                    # even when the director forgets to repeat them in its references field.
                    for m in workflow['modules']:
                        required = list(preferred)
                        for i in m['tests']:
                            for arg in self.all_tests[i]['argv']:
                                try:
                                    p = pathlib.Path(arg)
                                    rel = p.resolve().relative_to(self.core.workspace).as_posix() if p.is_absolute() else relative(arg)
                                    if rel in self.protected: required.append(rel)
                                except (ValueError, OSError): pass
                        refs = list(dict.fromkeys(m['references']+required))
                        m['references'] = [p for p in refs if p not in m['files']]
                        if len(m['references']) > MAX_REFERENCES: raise ValueError('Pflicht-Lesekontext überschreitet '+str(MAX_REFERENCES)+' Modulreferenzen.')
                    writes = {p for m in workflow['modules'] for p in m['files']}
                    if repair_feedback and not writes.issubset(self.authorized_files):
                        raise ValueError('Gesamtreparatur darf den ursprünglichen Schreibbereich nicht erweitern.')
                except ValueError as exc:
                    if value is None and not isinstance(exc, ModelOutputError): raise
                    self.unchanged(); self.core.checkpoint()
                    self.record_failure({'status':'failed','name':'Arbeitsplanung','output':redact(str(exc))},phase='planning')
                    if isinstance(exc, ModelOutputError):
                        self.receipt.setdefault('model_output_failures', []).append(exc.evidence)
                    self.receipt.setdefault('planning_errors', []).append(redact(str(exc)))
                    raw_plan = canonical(value)
                    redacted_plan = redact(raw_plan.decode('utf8'))
                    preview = truncate(redacted_plan, 12000)
                    self.receipt.setdefault('planning_failures', []).append({
                        'attempt':attempt+1, 'model':getattr(self.core,'role_models',{}).get('planner',getattr(self.core,'fallback_model',None) or self.model),
                        'path':getattr(exc,'path',None), 'error':redact(str(exc)),
                        'response_sha256':digest(raw_plan), 'response_bytes':len(raw_plan),
                        'response_preview':preview, 'preview_truncated':preview != redacted_plan})
                    self.save()
                    if attempt == self.limits['repairs']: raise
                    invalid_plans += 1
                    if invalid_plans >= 2 and 'planner' not in getattr(self.core,'role_models',{}):
                        previous = getattr(self.core,'fallback_model',None) or self.model
                        while self.fallback_index < len(self.fallbacks) and self.fallbacks[self.fallback_index] == previous:
                            self.fallback_index += 1
                        if self.fallback_index < len(self.fallbacks):
                            self.core.checkpoint(); self.unchanged()
                            replacement=self.fallbacks[self.fallback_index];self.fallback_index+=1
                            self.core.fallback_model=replacement
                            self.receipt.setdefault('model_switches',[]).append({'phase':'planning',
                                'from':previous,'to':replacement,'reason':'two_invalid_director_plans',
                                'remaining_model_calls':self.core.max_model_calls-self.core.model_calls,
                                'remaining_seconds':max(0,self.core.deadline-time.monotonic())})
                            self.core.log('Autonom: ungültige Planung an lokales Ersatzmodell '+replacement+' übergeben')
                            invalid_plans=0
                            self.save()
                    feedback = '\nPREVIOUS INVALID PLAN:\n'+truncate(json.dumps(value),4000)+'\nVALIDATION ERROR: '+redact(str(exc))+'\nCorrect this error without changing the original task. Return a concise plan, no implementation code or full data arrays.'
                    continue
                if value['questions']:
                    self.receipt.setdefault('planning_questions', []).append(value['questions'])
                    self.save()
                    if not clarification_refined and attempt < self.limits['repairs']:
                        clarification_refined = True
                        feedback = ('\nRECONSIDER PROPOSED QUESTIONS:\n'+json.dumps(value['questions'],ensure_ascii=False)+
                            '\nResolve reversible implementation and design choices yourself using the original task and read-only contracts. Missing code is the task to implement. Record decisions in assumptions. Keep questions only for indispensable external facts or permissions. Return the complete concise workflow.')
                        continue
                    raise RunStopped('blocked', 'Rückfrage: '+'; '.join(value['questions']))
                self.workflow = workflow
                if not repair_feedback:
                    self.original_workflow=copy.deepcopy(workflow)
                    self.original_plan=copy.deepcopy(value)
                if not repair_feedback: self.authorized_files = writes | (set(required_files)-set(self.protected))
                self.review_paths = getattr(self, 'review_paths', set()) | {
                    p for m in workflow['modules'] for p in m['files']+m['references']}
                for m in workflow['modules']:
                    for p in m['files']+m['references']:
                        self.expected.setdefault(p, identity(self.core.workspace,p))
                self.core.module_mode = True
                self.receipt['plan'] = value
                self.receipt['workflow'] = workflow
                self.receipt.setdefault('plan_history', []).append({'plan':value, 'workflow':workflow,
                    'input_identities':dict(self.core.read_identity), 'repair':bool(repair_feedback),
                    'interim_repair':interim_repair})
                self.receipt['planning_origin'] = 'model_from_original_task'
                self.receipt['workflow_sha256'] = digest(canonical(workflow))
                self.save()
                return
        finally:
            self.core.director_mode = False

    def execute_modules(self, start_index=0, *, finalize=True, future_modules=()):
        """No dependent work begins until its module and all earlier gates pass."""
        self.failed_module_index=None
        checked=list(dict.fromkeys(i for m in self.workflow['modules'][:start_index] for i in m['tests']))
        diagnosis_cache={}
        for number,module in enumerate(self.workflow['modules'][start_index:],start_index+1):
            self.core.checkpoint(); self.unchanged()
            self.module=module
            seen={p for m in self.workflow['modules'][:number] for p in m['files']}
            later=list(self.workflow['modules'][number:])+list(future_modules)
            self.core.future_artifacts = {} if self.request.get('workflow') else {p:m['id'] for m in reversed(later) for p in m['files'] if p not in seen}
            self.core.role_models=module['models']
            checked=list(dict.fromkeys(checked+module['tests']))
            self.core.config['tests']=[self.all_tests[i] for i in checked]
            self.receipt['active_module']=module['id']
            self.save()
            feedback=''
            review_failure=''
            failed_attempts=0
            for attempt in range(self.limits['repairs']+1):
                test=None
                proposal_failure=None
                self.core.allow_unchanged_module = attempt == 0 or bool(self.request.get('resume_from'))
                self.core.log('Modul '+module['id']+' Versuch '+str(attempt+1))
                task=('ORIGINAL USER TASK (implement only the current module):\n'+self.task+'\nGENERATED MODULE PLAN:\n'+module['contract']+'\nThe original requirements and unchanged test assertions are authoritative. Correct any conflicting implementation suggestion in the generated plan; preserve the bounded file scope.\nNur diese Dateien ändern: '+json.dumps(module['files']))
                if self.request.get('resume_from'):
                    task+='\nWIEDERAUFNAHME: '+self.request['changed_approach']+'\nDer geänderte Ansatz ist eine zu prüfende Hypothese, keine bestätigte Reparatur. Frühere Fehlerbelege:\n'+json.dumps(self.resume_evidence,ensure_ascii=False)
                if feedback: task+='\nTESTDIAGNOSE (vor nächstem Modul beheben):\n'+feedback
                try:
                    result,test=self.proposal(task)
                    if test.get('returncode')==0 or test.get('status')=='deferred':
                        self.unchanged()
                        context={}; review_bytes=0
                        for p in dict.fromkeys(module['files']+module['references']):
                            source=safe_path(self.core.workspace,p)
                            if not source.exists(): context[p]='[deleted or absent]'; continue
                            text, binary = self.core.read_reference(p)
                            if binary and p in module['files']:
                                raise RunStopped('blocked','Binärdatei ist kein Quelltext-Schreibziel: '+p)
                            size=len(text.encode('utf8')); review_bytes+=size
                            if size>20000 or review_bytes>80000: raise RunStopped('blocked','Modul-QC-Kontext zu groß: '+p)
                            context[p]=text
                        self.unchanged()
                        source_text='\n\n'.join('DATEI '+p+'\n'+(content if content is not None else '[deleted]') for p,content in context.items())
                        review_prompt='ORIGINAL TASK (scope this review to the current module):\n'+self.task+'\nMODULE PLAN (original requirements and test assertions take precedence):\n'+module['contract']+'\nIMPLEMENTATION AND READ-ONLY REFERENCES (test modes for other modules are not requirements for this module):\n'+source_text+'\nEXECUTED TESTS:\n'+test.get('output','')
                        review=self.core.chat('reviewer',review_prompt,self.model)
                        if review.get('verdict')!='ok':
                            self.core.log('Autonom: QC-Ablehnung am selben Quellstand gegenprüfen (zweite Modellprüfung)')
                            self.receipt['attempts'][-1]['initial_module_review']=review
                            review=self.core.chat('reviewer',review_prompt+'\nBESTRITTENE ABLEHNUNG / DISPUTED REVIEW:\n'+json.dumps(review,ensure_ascii=False)+'\nReconcile every claim with actual source behavior. Retain confirmed defects; withdraw claims contradicted by the source. Passing tests alone do not establish correctness.',self.model)
                        self.receipt['attempts'][-1]['module_review']=review
                        self.unchanged();self.core.checkpoint()
                        if review.get('verdict')!='ok':
                            review_failure=redact(json.dumps(review))
                            raise ProposalRejected(review_failure)
                        self.receipt['steps'].append({'number':number,'id':module['id'],
                            'status':'verified' if module['tests'] and test.get('status')!='deferred' else 'reviewed_pending_tests','proposal_id':result.receipt['tag'],
                            'tests':checked[:], 'after':{p:self.expected[p] for p in module['files'] if p in self.expected}})
                        if test.get('returncode') == 0:
                            self.unverified_attempts = 0
                            self.receipt['unverified_attempts'] = 0
                        self.save(); break
                    feedback=test.get('output','Test fehlgeschlagen')
                except ProposalRejected as exc:
                    proposal_failure=str(exc)
                    current_failure=self.latest.get('output','') if self.latest.get('returncode') not in (None,0) else ''
                    feedback=truncate(current_failure or review_failure,9000)+'\nVORSCHLAGFEHLER: '+str(exc)
                if test is not None and test.get('returncode') not in (None,0):
                    analysis=self.last_failure_analysis
                elif proposal_failure is not None:
                    analysis=self.record_failure({'output':proposal_failure,'status':'failed'},phase='proposal')
                else:
                    analysis=self.record_failure({'output':feedback,'status':'failed'})
                feedback+='\nPROGRAMMATISCHE FEHLERANALYSE (keine erfundene Reparaturanweisung):\n'+json.dumps({
                    k:analysis[k] for k in ('kind','known','locations','source_excerpts','comparison','unknown','next_action')},ensure_ascii=False)
                # One unresolved work budget spans changed symptoms, models and plans.
                # Only a test-backed, reviewed module establishes verified progress.
                self.unverified_attempts += 1
                self.receipt['unverified_attempts'] = self.unverified_attempts
                if self.unverified_attempts >= 4:
                    report = blocker_report(analysis, self.unverified_attempts)
                    report['attempt_scope'] = 'work_since_last_verified_module'
                    report['summary'] = ('Vier erfolglose Arbeitsversuche ohne verifiziertes Modul; '
                        'auch geänderte Fehlermeldungen oder neue Pläne setzen die Grenze nicht zurück. '
                        + analysis['known'][:500])
                    self.receipt['blocker_report'] = report
                    self.receipt.setdefault('module_failures', []).append({
                        'module': module['id'], 'attempt': attempt+1, 'diagnosis': feedback[:12000]})
                    self.final_feedback = None
                    self.save()
                    raise RunStopped('stalled', report['summary'])
                failed_attempts+=1
                if failed_attempts>=2 and attempt<self.limits['repairs'] and 'coder' not in module['models']:
                    previous=getattr(self.core,'fallback_model',None) or self.model
                    while self.fallback_index<len(self.fallbacks) and self.fallbacks[self.fallback_index]==previous:
                        self.fallback_index+=1
                    if self.fallback_index<len(self.fallbacks):
                        replacement=self.fallbacks[self.fallback_index];self.fallback_index+=1
                        self.core.checkpoint();self.unchanged()
                        self.core.fallback_model=replacement
                        self.receipt.setdefault('model_switches',[]).append({'module':module['id'],
                            'from':previous,'to':replacement,'reason':'two_unsuccessful_module_attempts',
                            'failure_fingerprint':analysis['fingerprint'],
                            'source':{p:self.expected.get(p) for p in module['files']},
                            'test_sha256':digest(canonical(test)),
                            'remaining_model_calls':self.core.max_model_calls-self.core.model_calls,
                            'remaining_seconds':max(0,self.core.deadline-time.monotonic())})
                        self.core.log('Autonom: lokales Ersatzmodell '+replacement+' innerhalb des bestehenden Budgets')
                        failed_attempts=0
                        self.save()
                # A rejected/no-op proposal is a separate attempt, but unchanged test
                # evidence may reuse its earlier advisory diagnosis without a new call.
                diagnostic_analysis=analyze_failure(self.core.workspace,self.latest.get('output',''),
                    status=self.latest.get('status','failed'),protected=self.protected)
                if attempt < self.limits['repairs'] and self.core.config.get('repair_diagnosis',True) and diagnostic_analysis['needs_model_diagnosis'] and self.latest.get('returncode') not in (None,0):
                    self.unchanged()
                    current=self.core.read_files(module['files']+module['references'], readonly_assets=True)
                    if self.core.truncated: raise RunStopped('blocked','Reparaturdiagnose benötigt vollständige Moduldateien.')
                    key=digest(canonical({'module':module['id'],'source':current,'test':self.latest.get('output',''),
                        'model':self.core.role_models.get('planner',getattr(self.core,'fallback_model',None) or self.model)}))
                    reused=key in diagnosis_cache
                    if not reused:
                        try:
                            diagnosis=self.core.chat('planner','DIAGNOSE A FAILED MODULE, do not create code. Determine the root cause from actual source and failing test. Return at most 3 short repair steps (600 characters each) including affected expressions, files limited to the module. Do not repeat the feature specification or propose changing tests.\nCONTRACT:\n'+module['contract']+'\nFAILED TEST:\n'+self.latest.get('output','')+'\nSOURCE:\n'+'\n\n'.join('FILE '+p+'\n'+text for p,text in current.items()),self.model)
                            plan=diagnosis.get('plan')
                            affected=diagnosis.get('files')
                            if not isinstance(affected,list) or not affected or any(
                                    not isinstance(p,str) or p not in module['files'] for p in affected):
                                raise ValueError('Diagnose darf nur explizite Schreibdateien des aktuellen Moduls betreffen.')
                            if not isinstance(plan,list) or not 1<=len(plan)<=3 or not all(isinstance(p,str) and p.strip() and len(p)<=600 for p in plan):
                                raise ValueError('Diagnose benötigt 1–3 nichtleere Reparaturschritte mit höchstens 600 Zeichen.')
                            diagnosis_cache[key]=plan
                        except RunStopped: raise
                        except (ValueError, RuntimeError) as exc:
                            # Diagnosis is advisory; actual failing tests remain authoritative.
                            # Do not spend another identical diagnosis call after a no-op.
                            diagnosis_cache[key]=[]
                            self.receipt.setdefault('diagnosis_errors',[]).append({'module':module['id'],
                                'input_sha256':key,'error':redact(str(exc)),
                                'action':'continue_bounded_coder_repair_with_original_test_failure'})
                    self.unchanged();self.core.checkpoint()
                    if diagnosis_cache[key]:
                        feedback+='\nUNVERIFIED DIAGNOSTIC HYPOTHESES (not established facts):\n'+'\n'.join(diagnosis_cache[key])
                        feedback+='\nCheck these hypotheses against the current source and the exact original test failure above. Discard contradicted advice. The original contract and unchanged tests remain authoritative; never modify tests.'
                    self.receipt.setdefault('repair_diagnoses',[]).append({'module':module['id'],'input_sha256':key,'reused':reused,'plan':diagnosis_cache[key]})
                self.receipt.setdefault('module_failures',[]).append({'module':module['id'],'attempt':attempt+1,'diagnosis':feedback[:12000]})
                self.save()
                if attempt==self.limits['repairs']:
                    self.failed_module_index=number-1
                    if not self.request.get('workflow') and self.latest.get('returncode') not in (None,0):
                        self.final_feedback = ('Cumulative integration test failed while implementing '+module['id']+
                            '. The defect may be in an earlier module; inspect the current implementation and repair its actual cause.\n'+feedback)
                    raise RunStopped('budget_exhausted','Modul '+module['id']+' nicht verifiziert; abhängige Schritte nicht gestartet.')
        self.module=None; self.core.role_models={}
        self.core.future_artifacts={}
        if not finalize: return
        self.core.config['tests']=self.all_tests
        self.latest=self.core.run_tests()
        self.unchanged(); self.core.checkpoint()
        self.record_test_progress(self.latest)
        if self.latest.get('returncode')!=0:
            self.record_failure(self.latest,phase='final_testing')
            num=self.numeric_failure_feedback(); self.final_feedback = 'Final tests failed:\n'+self.latest.get('output','')+('\n'+num if num else '')
            raise RunStopped('failed','Gesamtprüfung fehlgeschlagen; kein Abschluss.')
        paths=list(dict.fromkeys(list(self.tx.files)+sorted(getattr(self,'review_paths',set()))+[p for m in self.workflow['modules'] for p in m['files']+m['references']]))
        if len(paths)>int(self.core.config.get('context_max_files',40)):
            raise RunStopped('blocked','Zu viele Dateien für vollständiges Schlussreview.')
        context=self.core.read_files(paths, readonly_assets=True)
        if self.core.truncated or len(context)!=len(paths) or sum(len(t.encode('utf8')) for t in context.values())>80000:
            raise RunStopped('blocked','Gesamtergebnis zu groß oder unvollständig für vollständiges Review.')
        self.receipt['final_context']=dict(self.core.read_identity)
        self.unchanged()
        source_text='\n\n'.join('FILE '+p+'\n'+t for p,t in context.items())
        review=self.core.chat('reviewer','Review the entire result against the ORIGINAL TASK. Read-only references are context, not additional write requirements. Reject demonstrated missing functionality.\n'+self.task+'\nFILES:\n'+source_text+'\nEXECUTED TESTS:\n'+self.latest.get('output',''),self.model)
        self.receipt['final_review']=review
        self.unchanged(); self.core.checkpoint()
        if review.get('verdict')!='ok':
            self.record_failure({'output':json.dumps(review.get('issues',[])), 'status':'failed'},phase='final_review')
            self.final_feedback = 'Final review rejected:\n'+redact(json.dumps(review))
            raise RunStopped('failed','Gesamt-QC hat den Originalauftrag nicht bestätigt: '+redact(json.dumps(review)))
        self.receipt['active_module']=None
        self.receipt['status']='succeeded'
        self.receipt['completion_basis']='All module contracts reviewed, cumulative gates and complete configured suite passed; external product acceptance remains separate.'
