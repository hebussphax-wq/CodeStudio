"""Deterministic failure evidence. No model calls and no inferred code fixes."""
import pathlib
import re
from safety import canonical, digest, redact, safe_path, read_text


def analyze_failure(workspace, output, *, status='failed', profile='', profile_id='', phase='', module=None, protected=()):
    text=redact(str(output))
    matches=re.findall(r'^\s*((?:Assertion|Syntax|Indentation|Type|Reference|Name|Value|Import|ModuleNotFound|FileNotFound|Permission|Runtime|Timeout)Error)(?:\s*\[[^\]]+\])?\s*:?\s*([^\r\n]*)',text,re.M)
    error,message=matches[-1] if matches else ('', '')
    if status in ('timed_out','timeout') or error=='TimeoutError' or re.search(r'\btimed out\b|Zeitbudget|^Timeout$',text,re.I|re.M): kind='timeout'
    elif error=='AssertionError': kind='assertion'
    elif error in ('SyntaxError','IndentationError'): kind='syntax'
    elif error in ('FileNotFoundError','ModuleNotFoundError','ImportError') or 'ENOENT' in text: kind='dependency'
    elif error=='PermissionError': kind='permission'
    elif 'Keine effektive Änderung' in text or 'kein Fortschritt' in text: kind='no_progress'
    elif error: kind='runtime'
    else: kind='unknown'
    locations=[]
    candidates=re.findall(r'File "([^"\r\n]+)", line (\d+)',text)
    candidates+=re.findall(r'((?:[A-Za-z]:[\\/]|/)[^\r\n()]*?\.(?:py|[cm]?js|tsx?|jsx)):(\d+)(?::\d+)?',text)
    root=pathlib.Path(workspace).resolve()
    for value,line in candidates:
        try:
            path=pathlib.Path(value).resolve().relative_to(root).as_posix()
            safe_path(root,path)
        except (ValueError,OSError): continue
        loc={'path':path,'line':int(line)}
        if loc not in locations: locations.append(loc)
    checks=[loc for loc in locations if loc['path'] in protected]
    locations=(checks+locations[-3:]) if checks else locations[-3:]
    locations=list({(loc['path'],loc['line']):loc for loc in locations}.values())[:3]
    excerpts=[]
    for loc in locations:
        try:
            p=safe_path(root,loc['path'])
            if p.stat().st_size>20000: continue
            lines=read_text(p).splitlines();number=loc['line']
            excerpts.append({'path':loc['path'],'line':number,
                             'text':redact('\n'.join(lines[max(0,number-2):number+1]))[:1000]})
        except (OSError,ValueError): pass
    comparison={}
    for name in ('actual','expected','operator'):
        found=re.search(r'^\s*'+name+r':\s*([^\r\n]+)',text,re.M)
        if found: comparison[name]=found.group(1).rstrip(',')[:160]
    # Actual values and stack noise may change while the same check still fails.
    # Only passing that check establishes progress; rewriting a file does not.
    signature={'kind':kind,'profile':profile_id or profile,'error':error,
               'message':'' if checks and kind=='assertion' else re.sub(r'\b\d+(?:\.\d+)?\b','?',message[:500]),
               'locations':checks or [{'path':loc['path']} for loc in locations]}
    if kind=='unknown': signature['message']=re.sub(r'\b\d+(?:\.\d+)?\b','?',text[:500])
    recommendations={
        'timeout':'Laufzeit des genannten Programms und dessen Ausgabe prüfen; Zeitlimit erst nach Ursachenprüfung ändern.',
        'assertion':'Genannte Testbedingung mit der Implementierung vergleichen. Vorhandene Tests beibehalten.',
        'syntax':'Syntaxfehler an der gemeldeten Quellstelle beheben und dasselbe Testprofil erneut prüfen.',
        'dependency':'Gemeldete Datei oder Abhängigkeit und ihre Zuständigkeit prüfen. Keine automatische Installation.',
        'permission':'Zielpfad und bestehende Zugriffsrechte prüfen; keine Rechte automatisch erweitern.',
        'no_progress':'Identische oder wirkungslose Änderungen nicht erneut anwenden; Reparaturstrategie überprüfen.',
        'runtime':'Gemeldete Ausnahme und Quellstelle prüfen; erst daraus die Reparatur ableiten.',
        'unknown':'Ausgabe und Arbeitsvertrag prüfen; eine begrenzte Modellanalyse kann die unklare Ursache untersuchen.'}
    known=message or (error if error else text[:600])
    return {'schema':'codestudio.failure-analysis.v1','fingerprint':digest(canonical(signature)),
            'kind':kind,'phase':phase,'module':module,'test_profile':profile,
            'locations':locations,'source_excerpts':excerpts,'comparison':comparison,
            'known':known,'unknown':'Die genaue Implementierungsursache ist durch diese Meldung allein nicht bewiesen.',
            'evidence':text[:3000], 'next_action':recommendations[kind],
            'needs_model_diagnosis':kind=='unknown','analysis_method':'programmatic'}


def blocker_report(analysis, attempts):
    where=', '.join(x['path']+':'+str(x['line']) for x in analysis['locations'])
    subject=analysis.get('test_profile') or analysis.get('module') or analysis.get('phase') or 'Auftrag'
    return {**analysis,'attempts':attempts,
            'summary':f'{subject}: {attempts} erfolglose Versuche am selben Problem. '+analysis['known'][:500],
            'location':where or 'Keine eindeutige Quellstelle in der Programmausgabe.',
            'decision':'Auftrag gestoppt; keine weitere automatische Wiederholung dieses Problems.'}
