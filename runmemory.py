"""Local evidence, never executable rules: preserve and validate resumable candidates."""
import json
import re
from safety import atomic_bytes, canonical, digest, identity, relative, safe_path
from moduleflow import normalize_workflow

MAX_BYTES = 4 * 1024 * 1024

def failure_history(*groups):
    unique={}
    for group in groups:
        for row in group:
            key=row.get('fingerprint') or digest(canonical(row))
            unique.pop(key,None);unique[key]=row
    return list(unique.values())[-12:]

def run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('Ungültige Quellauftrags-ID.')
    return value

def snapshot_path(runs, value):
    return runs / ('candidate-' + run_id(value) + '.json')

def save_candidate(run):
    if not run.tx.files or not getattr(run, 'original_workflow', None): return None
    run.unchanged()
    if run.core.runs.resolve().is_relative_to(run.core.workspace):
        run.receipt['candidate_unavailable']='Belegspeicher liegt im Projekt; sichere Wiederaufnahme benötigt einen Speicher außerhalb.'
        return None
    files = {}
    basis = dict(run.expected)
    for rel, entry in run.tx.files.items():
        current = identity(run.core.workspace, rel)
        if current != entry['sha256_after']: raise ValueError('Kandidat wurde fremd verändert: ' + rel)
        data = safe_path(run.core.workspace, rel, True).read_bytes() if current is not None else None
        files[rel] = {'before':entry['sha256_before'], 'after':current,
                      'content':data.decode('utf8') if data is not None else None}
        basis[rel] = entry['sha256_before']
    payload = {'schema':'codestudio.candidate.v1', 'run_id':run.id,
        'workspace':str(run.core.workspace), 'task':run.task, 'workflow':run.original_workflow,
        'plan':getattr(run,'original_plan',None),
        'tests_sha256':run.receipt['tests_sha256'], 'basis':basis, 'protected':run.protected,
        'files':files, 'model':run.model, 'options':run.core.config.get('options',{}),
        'failure_analysis':failure_history(getattr(run,'resume_evidence',[]),
            [{**row,'source_run':run.id} for row in run.receipt.get('failure_analysis',[])]),
        'failed_candidates':run.failed_candidates[-48:], 'status':'unverified_candidate'}
    raw = canonical(payload)
    if len(raw)>MAX_BYTES: raise ValueError('Kandidatensicherung überschreitet 4 MiB.')
    envelope = {'sha256':digest(raw), 'payload':payload}
    path = snapshot_path(run.core.runs, run.id)
    atomic_bytes(path, canonical(envelope))
    if json.loads(path.read_bytes()) != envelope: raise ValueError('Kandidatensicherung nicht rücklesbar.')
    run.receipt['candidate'] = {'path':str(path), 'sha256':envelope['sha256'],
        'status':'unverified_candidate', 'files':list(files)}
    return payload

def load_candidate(runs, source_id, workspace, tests, max_steps=12, _depth=0):
    source_id=run_id(source_id)
    receipt_path=runs / ('autonomous-' + source_id + '.json')
    if receipt_path.stat().st_size>16*MAX_BYTES: raise ValueError('Quellbeleg zu groß.')
    receipt=json.loads(receipt_path.read_bytes())
    if (receipt.get('run_id')!=source_id or receipt.get('workspace')!=str(workspace)
        or receipt.get('status') not in ('stalled','failed','budget_exhausted','timed_out','cancelled','blocked')
        or receipt.get('rollback_verified') is not True):
        raise ValueError('Wiederaufnahme benötigt einen beendeten, bestätigt zurückgerollten Auftrag.')
    path=snapshot_path(runs,source_id)
    if path.stat().st_size>MAX_BYTES*2: raise ValueError('Kandidatensicherung zu groß.')
    envelope=json.loads(path.read_bytes()); p=envelope.get('payload',{})
    if (digest(canonical(p))!=envelope.get('sha256') or
        receipt.get('candidate',{}).get('sha256')!=envelope.get('sha256')):
        raise ValueError('Kandidatensicherung beschädigt oder nicht an den Quellbeleg gebunden.')
    if (p.get('schema')!='codestudio.candidate.v1' or p.get('run_id')!=source_id or
        p.get('workspace')!=str(workspace) or p.get('tests_sha256')!=digest(canonical(tests))):
        raise ValueError('Projekt oder Testprofile passen nicht zum Kandidaten.')
    if not isinstance(p.get('task'),str) or not p['task'].strip(): raise ValueError('Originalauftrag fehlt.')
    workflow=normalize_workflow(p['workflow'],len(tests),max_steps,allow_pending_tests=True)
    allowed={f for m in workflow['modules'] for f in m['files']}
    if set(p['files'])-allowed or len(p['files'])>32: raise ValueError('Kandidat außerhalb des Originalworkflows.')
    if not set(p['protected']).issubset(p['basis']): raise ValueError('Prüfidentitäten fehlen.')
    for rel, expected in p['basis'].items():
        relative(rel)
        if identity(workspace,rel)!=expected: raise ValueError('Ausgangsdatei geändert; Kandidat nicht übernommen: '+rel)
    for rel, entry in p['files'].items():
        relative(rel)
        if rel in p['protected']: raise ValueError('Kandidat verändert geschützte Prüfdatei.')
        content=entry['content']
        if content is not None and not isinstance(content,str): raise ValueError('Ungültiger Kandidateninhalt.')
        if (digest(content.encode('utf8')) if content is not None else None)!=entry['after']:
            raise ValueError('Kandidateninhalt beschädigt: '+rel)
        if rel not in p['basis'] or entry['before']!=p['basis'][rel]: raise ValueError('Kandidatenbasis unvollständig.')
    # Reuse complete prior model plans only when their recorded content hash matches.
    # They remain proposals and must pass the current planner validator again.
    previous=receipt.get('resume',{}).get('source_run')
    if previous and previous!=source_id and _depth<3:
        try:
            older=load_candidate(runs,previous,workspace,tests,max_steps,_depth+1)
            p['failure_analysis']=failure_history(
                [{**row,'source_run':row.get('source_run',previous)} for row in older.get('failure_analysis',[])],p.get('failure_analysis',[]))
        except (OSError,ValueError,KeyError,TypeError):
            pass  # Incompatible older evidence is never authority for this candidate.
    p['_planning_candidates']=[]
    for row in receipt.get('planning_failures',[])[-4:]:
        try:
            if row.get('preview_truncated'): continue
            proposal=json.loads(row['response_preview'])
            if digest(canonical(proposal))==row['response_sha256'] and isinstance(proposal,dict):
                p['_planning_candidates'].append(proposal)
        except (KeyError,TypeError,ValueError): pass
    return p

def candidate_signature(run, final_state):
    hashes={p:identity(run.core.workspace,p) for p in set(run.tx.files)|set(final_state)}
    hashes.update({p:digest(s.encode('utf8')) if s is not None else None for p,s in final_state.items()})
    return digest(canonical({'files':hashes,'tests':run.receipt['tests_sha256'],
        'protected':run.protected,'active_tests':run.core.config['tests'],
        'module':run.module['id'] if run.module else None}))


def record_success(run):
    if not run.request.get('resume_from') or run.receipt.get('status')!='succeeded': return
    if run.latest.get('returncode')!=0 or run.receipt.get('final_review',{}).get('verdict')!='ok':
        raise ValueError('Lernerfolg benötigt frische Gesamttests und Schluss-QC.')
    run.unchanged()
    evidence={'schema':'codestudio.experience.v1','status':'verified_run_outcome',
        'source_run':run.request['resume_from'],'successful_run':run.id,
        'workspace':str(run.core.workspace),'task_sha256':digest(run.task.encode('utf8')),
        'tests_sha256':run.receipt['tests_sha256'],'protected':run.protected,
        'after':{p:identity(run.core.workspace,p) for p in run.tx.files},
        'approach_hypothesis':run.request['changed_approach'],
        'model_history':run.core.model_history,'scope':'Evidence for this exact run; no general repair rule or model training.'}
    path=run.core.runs / ('experience-'+run.id+'.json')
    atomic_bytes(path,canonical(evidence))
    run.receipt['experience']={'path':str(path),'sha256':digest(canonical(evidence)),
        'status':'verified_run_outcome'}
