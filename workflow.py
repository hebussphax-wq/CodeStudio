"""Shared job envelope for CodeStudio, TobyKi, HaloMonsterAI and BizDrive workers."""
import copy
import re
from safety import digest, canonical, ensure_source_text

PRODUCTS = ('codestudio','tobyki','halomonsterai','bizdrive')
def normalize_job(job):
    if not isinstance(job,dict) or job.get('schema')!='codestudio.job.v1':
        raise ValueError('codestudio.job.v1 erforderlich.')
    if job.get('product') not in PRODUCTS: raise ValueError('Unbekanntes Hostprodukt.')
    for key in ('project_id','task_id','workflow_id','employee_id'):
        if not isinstance(job.get(key),str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',job[key]):
            raise ValueError('Gebundene Kennung erforderlich: '+key)
    task=job.get('task')
    acceptance=job.get('acceptance')
    if not isinstance(task,str) or not task.strip() or not isinstance(acceptance,list) or not acceptance or not all(isinstance(x,str) and x.strip() for x in acceptance):
        raise ValueError('Auftrag und Akzeptanzkriterien erforderlich.')
    ensure_source_text(task+'\n'+'\n'.join(acceptance))
    if job.get('execution_authorized') is not True:
        raise ValueError('Host muss diesen autonomen Entwicklungsauftrag freigegeben haben.')
    # The envelope records host workflow ownership; it is not an Owner/session bypass.
    return {k:copy.deepcopy(job[k]) for k in ('schema','product','project_id','task_id','workflow_id','employee_id','task','acceptance','execution_authorized')}

def job_result(job,receipt,receipt_path):
    normalized=normalize_job(job)
    return {'schema':'codestudio.job-result.v1',
            **{k:normalized[k] for k in ('product','project_id','task_id','workflow_id','employee_id')},
            'job_sha256':digest(canonical(normalized)), 'run_id':receipt['run_id'],
            'execution_status':receipt['status'],
            'workflow_next':'verify_and_review' if receipt['status']=='succeeded' else 'inspect_failure',
            'host_accepted':False, 'receipt_path':receipt_path,
            'receipt_sha256':digest(canonical(receipt)),
            'changed_files':list(receipt.get('after',{})),
            'test_status':receipt.get('test',{}).get('status'),
            'rollback_verified':receipt.get('rollback_verified',False)}
