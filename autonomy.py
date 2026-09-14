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
from core import CodeStudioCore, truncate, SCHEMA
from workflow import normalize_job, job_result
from moduleflow import normalize_workflow
from processrunner import run_command, ProcessTreeUncertain
from safety import WorkspaceTransaction, atomic_bytes, canonical, digest, identity, relative, redact, ensure_source_text

class RunStopped(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)

class ProposalRejected(RuntimeError):
    pass

class AutonomousCore(CodeStudioCore):
    def checkpoint(self):
        if self.cancel_event.is_set():
            raise RunStopped('cancelled', 'Autonomer Auftrag abgebrochen.')
        if time.monotonic() >= self.deadline:
            raise RunStopped('timed_out', 'Zeitbudget des autonomen Auftrags erreicht.')

    def chat(self, role, user, model):
        self.checkpoint()
        model = getattr(self, 'role_models', {}).get(role, model)
        if self.model_calls >= self.max_model_calls:
            raise RunStopped('budget_exhausted', 'Modellaufruf-Budget erreicht.')
        self.model_calls += 1
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
        return super().ollama_request(path,body,timeout=min(timeout or self.config.get('timeout_sec',900),max(.1,self.deadline-time.monotonic())))

    def run_tests(self):
        self.checkpoint()
        results = []
        for profile in self.config['tests']:
            self.checkpoint()
            self.log('Autonom: Projekttest '+str(profile.get('name') or profile['argv'][0]))
            timeout = min(profile.get('timeout_sec', 300), max(.1, self.deadline-time.monotonic()))
            outcome = run_command(profile['argv'], self.workspace, timeout, cancel_event=self.cancel_event)
            if outcome['returncode']==0 and re.search(r'\bRan 0 tests?\b',outcome['output']):
                outcome={**outcome,'returncode':126,'status':'no_tests','output':outcome['output']+'\nKeine Tests ausgeführt; kein autonomer Abschluss.'}
            row = {**outcome, 'name':profile.get('name') or profile['argv'][0],
                   'output':truncate(redact(outcome['output']), 12000)}
            results.append(row)
            if outcome['returncode'] != 0:
                return {**row, 'results':results}
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
        self.task = (self.job['task']+'\nAKZEPTANZ:\n'+'\n'.join(self.job['acceptance'])) if self.job else request.get('task')
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
        tests = config.get('tests',[])
        if not tests: raise ValueError('Autonomes Entwickeln benötigt konfigurierte Projekttests.')
        for t in tests:
            if not isinstance(t,dict) or not isinstance(t.get('argv'),list) or not t['argv'] or not all(isinstance(a,str) and a for a in t['argv']):
                raise ValueError('Testprofil benötigt Programm und Argumente.')
            if pathlib.Path(t['argv'][0]).suffix.lower() in ('.bat','.cmd'):
                raise ValueError('Direktes Testprogramm statt Batch-Datei einstellen.')
            if type(t.get('timeout_sec',300)) is not int or not 1<=t.get('timeout_sec',300)<=3600:
                raise ValueError('Ungültiger Test-Timeout.')
        self.workflow = normalize_workflow(request['workflow'], len(tests), self.limits['steps']) if 'workflow' in request else None
        self.all_tests = copy.deepcopy(tests)
        self.module = None
        self.emit = emit
        self.cancel_event = threading.Event()
        self.core = AutonomousCore(root, config, log=lambda msg:emit({'event':'log','text':redact(msg)}), transport=transport)
        self.core.cancel_event = self.cancel_event
        self.core.deadline = time.monotonic()+self.limits['seconds']
        self.core.model_calls = 0
        self.core.max_model_calls = self.limits['model_calls']
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

    def save(self):
        self.receipt['model_calls'] = self.core.model_calls
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
                   'files':list(result.final_state),'after':{p:self.expected[p] for p in result.final_state}}
        if self.module: attempt['module_id'] = self.module['id']
        self.receipt['attempts'].append(attempt)
        self.save()  # Durable before any test program starts.
        test = self.core.run_tests()
        attempt['test'] = test
        self.latest = test
        self.unchanged()
        self.core.checkpoint()
        self.save()
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
            if self.workflow:
                self.execute_modules()
            else:
                plan = self.core.chat('planner','AUTONOMER GESAMTAUFTRAG:\n'+self.task+
                    '\nZerlege in maximal '+str(self.limits['steps'])+' zusammenhängende Implementierungsschritte. '
                    'Jeder plan-Eintrag ist ein konkreter Teilauftrag. Keine Schritte nur zum Ausführen von Tests, Review oder Freigeben: das übernimmt die Laufzeit. '
                    'Keine Rückfrage außer fehlenden zwingenden Angaben.\nDATEIBAUM:\n'+'\n'.join(tree), self.model)
                if plan.get('questions'): raise RunStopped('blocked','Rückfrage: '+redact(str(plan['questions'])))
                steps = plan.get('plan')
                if not isinstance(steps,list) or not steps or len(steps)>self.limits['steps'] or not all(isinstance(s,str) and s.strip() for s in steps):
                    raise ValueError('Plan muss nichtleere Schritte innerhalb des Budgets enthalten.')
                if not isinstance(plan.get('acceptance'),list) or not plan['acceptance']:
                    raise ValueError('Plan benötigt überprüfbare Akzeptanzkriterien.')
                self.receipt['plan'] = plan
                self.save()
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
                    self.save()
                if not self.latest: self.latest = self.core.run_tests()
                for repair in range(self.limits['repairs']+1):
                    self.core.checkpoint()
                    self.unchanged()
                    if self.latest.get('returncode') == 0:
                        context = self.core.read_files(list(self.tx.files))
                        review = self.core.chat('reviewer','Gesamtergebnis gegen Originalauftrag prüfen. Verbleibende Fehler/Vollständigkeitslücken benennen.\nAUFTRAG:\n'+self.task+
                            '\nAKZEPTANZ:\n'+json.dumps(plan['acceptance'],ensure_ascii=False)+
                            '\nAKTUELLE DATEIEN:\n'+json.dumps(context,ensure_ascii=False)+
                            '\nTESTERGEBNIS:\n'+self.latest['output'],self.model)
                        self.receipt['final_review'] = review
                        if review.get('verdict')=='ok':
                            self.unchanged()
                            self.core.checkpoint()
                            self.receipt['status']='succeeded'
                            break
                        feedback = redact(json.dumps(review,ensure_ascii=False))
                    else:
                        feedback = 'Projekttest fehlgeschlagen. Aktuell fehlerhafter Code ist noch im Workspace. Repariere ihn, vorhandene Tests nicht ändern.\n'+self.latest.get('output','')
                    if repair == self.limits['repairs']:
                        raise RunStopped('budget_exhausted','Reparaturbudget erreicht; Ziel noch nicht verifiziert.')
                    self.core.log('Autonom: Reparatur '+str(repair+1)+'/'+str(self.limits['repairs']))
                    try:
                        self.proposal(self.task+'\n\nAUTOMATISCHE REPARATUR:\n'+feedback+'\nBisherige Schritte: '+json.dumps(self.receipt['steps'],ensure_ascii=False))
                    except ProposalRejected as exc:
                        self.receipt.setdefault('repair_errors',[]).append(str(exc))
                        self.core.log('Reparaturvorschlag ungültig: '+str(exc))
                        self.save()
            self.receipt['after'] = {p:identity(self.core.workspace,p) for p in self.tx.files}
            self.receipt['test'] = self.latest
            self.receipt['finished_at'] = time.time()
            self.save()
            atomic_bytes(self.tx.root/'effect-receipt.json',canonical(self.receipt))
        except Exception as exc:
            self.receipt['error'] = redact(str(exc))
            self.receipt['status'] = exc.status if isinstance(exc,RunStopped) else 'failed'
            self.receipt['test'] = self.latest
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
            self.receipt['finished_at']=time.time()
            self.receipt['updated_at']=self.receipt['finished_at']
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

    def execute_modules(self):
        """No dependent work begins until its module and all earlier gates pass."""
        checked=[]
        for number,module in enumerate(self.workflow['modules'],1):
            self.core.checkpoint(); self.unchanged()
            self.module=module
            self.core.role_models=module['models']
            checked=list(dict.fromkeys(checked+module['tests']))
            self.core.config['tests']=[self.all_tests[i] for i in checked]
            self.receipt['active_module']=module['id']
            self.save()
            feedback=''
            for attempt in range(self.limits['repairs']+1):
                self.core.log('Modul '+module['id']+' Versuch '+str(attempt+1))
                task=module['contract']+'\nNur diese Dateien ändern: '+json.dumps(module['files'])
                if feedback: task+='\nTESTDIAGNOSE (vor nächstem Modul beheben):\n'+feedback
                try:
                    result,test=self.proposal(task)
                    if test.get('returncode')==0:
                        self.receipt['steps'].append({'number':number,'id':module['id'],
                            'status':'verified','proposal_id':result.receipt['tag'],
                            'tests':checked[:], 'after':{p:self.expected[p] for p in module['files'] if p in self.expected}})
                        self.save(); break
                    feedback=test.get('output','Test fehlgeschlagen')
                except ProposalRejected as exc:
                    feedback=str(exc)
                self.receipt.setdefault('module_failures',[]).append({'module':module['id'],'attempt':attempt+1,'diagnosis':feedback[:12000]})
                self.save()
                if attempt==self.limits['repairs']:
                    raise RunStopped('budget_exhausted','Modul '+module['id']+' nicht verifiziert; abhängige Schritte nicht gestartet.')
        self.module=None; self.core.role_models={}
        self.core.config['tests']=self.all_tests
        self.latest=self.core.run_tests()
        self.unchanged(); self.core.checkpoint()
        if self.latest.get('returncode')!=0:
            raise RunStopped('failed','Gesamtprüfung fehlgeschlagen; kein Abschluss.')
        context=self.core.read_files(list(self.tx.files))
        if self.core.truncated: raise RunStopped('blocked','Gesamtergebnis zu groß für vollständiges Review.')
        review=self.core.chat('reviewer','Gesamtergebnis gegen ORIGINALAUFTRAG prüfen. Fehlende Funktionen zurückweisen.\n'+self.task+'\nDATEIEN:\n'+json.dumps(context,ensure_ascii=False)+'\nTESTS:\n'+self.latest.get('output',''),self.model)
        self.receipt['final_review']=review
        self.unchanged(); self.core.checkpoint()
        if review.get('verdict')!='ok': raise RunStopped('failed','Gesamt-QC hat den Originalauftrag nicht bestätigt: '+redact(json.dumps(review)))
        self.receipt['active_module']=None
        self.receipt['status']='succeeded'
        self.receipt['completion_basis']='All module contracts reviewed, cumulative gates and complete configured suite passed; external product acceptance remains separate.'
