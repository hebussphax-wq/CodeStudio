"""Caller-bound module contracts: models cannot expand writes or select commands."""
import copy
from safety import relative, ensure_source_text
MAX_REFERENCES = 24

def normalize_workflow(value, test_count, max_steps, allow_pending_tests=False):
    if not isinstance(value, dict) or value.get('schema') != 'codestudio.modules.v1':
        raise ValueError('Workflow benötigt codestudio.modules.v1.')
    modules=value.get('modules')
    if not isinstance(modules,list) or not 1<=len(modules)<=max_steps:
        raise ValueError('Workflow-Modulanzahl außerhalb des Budgets.')
    seen=set(); result=[]
    for row in modules:
        if not isinstance(row,dict): raise ValueError('Ungültiges Modul.')
        name=row.get('id'); contract=row.get('contract'); paths=row.get('files'); refs=row.get('references',[])
        if not isinstance(name,str) or not name.strip() or len(name)>80 or name in seen:
            raise ValueError('Eindeutige Modul-ID erforderlich.')
        if not isinstance(contract,str) or not contract.strip() or len(contract)>10000:
            raise ValueError('Begrenzter Modulvertrag erforderlich.')
        ensure_source_text(contract)
        for values,limit in ((paths,4),(refs,MAX_REFERENCES)):
            if not isinstance(values,list) or len(values)>limit or not all(isinstance(p,str) for p in values):
                raise ValueError('Begrenzte Modulpfade erforderlich.')
            if len({relative(p).casefold() for p in values})!=len(values): raise ValueError('Mehrdeutige Modulpfade.')
        if not paths: raise ValueError('Modul benötigt Schreibpfade.')
        # SoftKI: write wins - drop references that are also write targets before validate.
        write_fold = {relative(p).casefold() for p in paths}
        refs = [p for p in refs if relative(p).casefold() not in write_fold]
        dependencies=row.get('depends_on',[])
        if not isinstance(dependencies,list) or any(not isinstance(d,str) or d not in seen for d in dependencies):
            raise ValueError('Abhängigkeiten müssen vorherige Module bezeichnen.')
        checks=row.get('tests')
        if not isinstance(checks,list) or (not checks and not allow_pending_tests) or any(type(i) is not int or not 0<=i<test_count for i in checks):
            raise ValueError('Modul benötigt freigegebene Testprofil-Indizes.')
        models=row.get('models',{})
        if not isinstance(models,dict) or any(k not in ('planner','coder','reviewer') or not isinstance(v,str) or not v.strip() for k,v in models.items()):
            raise ValueError('Ungültige Rollenmodelle.')
        result.append({'id':name,'contract':contract,'files':[relative(p) for p in paths],
                       'references':[relative(p) for p in refs],'depends_on':dependencies,
                       'tests':list(dict.fromkeys(checks)),'models':models})
        seen.add(name)
    return {'schema':'codestudio.modules.v1','modules':copy.deepcopy(result)}

def validate_profiles(profiles):
    if not isinstance(profiles,list) or not 1<=len(profiles)<=24: raise ValueError('1–24 Testprofile erforderlich.')
    import pathlib
    for t in profiles:
        if not isinstance(t,dict) or not isinstance(t.get('argv'),list) or not t['argv'] or not all(isinstance(a,str) and a for a in t['argv']): raise ValueError('Testprofil benötigt Argumentliste.')
        if pathlib.Path(t['argv'][0]).suffix.lower() in ('.bat','.cmd'): raise ValueError('Direktes Testprogramm erforderlich.')
        if type(t.get('timeout_sec',300)) is not int or not 1<=t.get('timeout_sec',300)<=3600: raise ValueError('Ungültiger Test-Timeout.')
    return copy.deepcopy(profiles)
