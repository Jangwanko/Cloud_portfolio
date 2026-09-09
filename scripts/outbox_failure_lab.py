"""Run real process-exit boundaries against a candidate image in an isolated lab."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

from release_failure_lab import Lab, ROOT, metadata, pod_spec, deployment, env


def run(context, image):
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lab = Lab(context,image,run_id)
    report = {'result':'FAIL','run_id':run_id,'image':image,'context':context,'checks':{},
              'source_commit':lab.command(['git','rev-parse','HEAD']).strip(),
              'source_dirty':True,
              'scope':'Isolated local candidate; real Kafka/PostgreSQL and abrupt Python process exits. No production SLA or external channel delivery claim.'}
    output = ROOT/'results'/'outbox-failure'/run_id
    output.mkdir(parents=True)
    def save(name, value):
        (output/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    def probe(action, *args, exit_code=0):
        command=['kubectl','--context',context,'exec','-n',lab.namespace,'probe','--',
                 'python','/fault/probe.py',action,*args]
        r=subprocess.run(command,capture_output=True,text=True,timeout=100)
        if r.returncode != exit_code:
            raise RuntimeError(f'{action}: exit {r.returncode}, expected {exit_code}; {r.stderr[-1500:]}')
        if exit_code:
            result={'exit_code':r.returncode}
        else:
            result=json.loads(r.stdout)
        save(action+'-'+str(time.time_ns()),result)
        return result
    def scale(name, replicas):
        lab.kubectl('scale','deployment/'+name,'-n',lab.namespace,'--replicas='+str(replicas))
        if replicas == 0:
            lab.wait(name+' stopped',lambda:not lab.get('pods',namespace=lab.namespace)['items'] or
                not any(p['metadata'].get('labels',{}).get('app')==name
                        for p in lab.get('pods',namespace=lab.namespace)['items']))
    def state(messages,pending,notifications):
        s=probe('snapshot')
        return s if (s['messages'],s['pending'],s['notifications'])==(messages,pending,notifications) else None
    try:
        lab.bootstrap()
        lab.successful_sync('0.1.0')
        report['control_before']=lab.report['control_before']
        code=(ROOT/'scripts/outbox_fault_probe.py').read_text()
        lab.apply({'apiVersion':'v1','kind':'ConfigMap','metadata':metadata('outbox-fault',lab.namespace),
                   'data':{'probe.py':code}})
        # Probe Pod has no controller; replace only this disposable Pod to mount fault code.
        lab.kubectl('delete','pod','probe','-n',lab.namespace,'--wait=true')
        spec=pod_spec(image,['python','-c','import time; time.sleep(86400)'],app_env=True)
        for name,cm,path in [('probe-code','probe-code','/probe'),('fault','outbox-fault','/fault')]:
            spec['volumes'].append({'name':name,'configMap':{'name':cm}})
            spec['containers'][0]['volumeMounts'].append({'name':name,'mountPath':path})
        lab.apply({'apiVersion':'v1','kind':'Pod','metadata':metadata('probe',lab.namespace),'spec':spec})
        lab.kubectl('wait','-n',lab.namespace,'pod/probe','--for=condition=Ready','--timeout=120s',timeout=130)
        scale('worker',0)
        probe('seed','1')
        probe('commit-crash',exit_code=71)
        assert state(1,1,0)
        report['checks']['commit_crash_retains_event_and_intent']=True
        assert probe('fail-send')['pending']==1
        report['checks']['send_failure_retains_intent']=True
        probe('ack-crash',exit_code=72)
        lab.wait('first notification persisted',lambda:state(1,1,1))
        spec=pod_spec(image,['python','-m','worker.main'],app_env=True)
        spec['containers'][0]['env']=env({'WORKER_MODE':'outbox'})
        lab.apply(deployment('outbox-publisher',lab.namespace,spec))
        scale('worker',1)
        lab.wait('ACK crash recovered',lambda:state(1,0,1))
        report['checks']['ack_crash_republished_without_duplicate_attempt']=True
        scale('worker',0)
        probe('seed','1')
        probe('rollback-crash',exit_code=73)
        assert state(1,0,1)
        report['checks']['precommit_crash_rolls_back_event_and_intent']=True
        scale('worker',1)
        lab.wait('precommit replay recovered',lambda:state(2,0,2))
        scale('outbox-publisher',0)
        probe('seed','10')
        lab.wait('concurrency backlog',lambda:state(12,10,2))
        scale('outbox-publisher',2)
        lab.wait('two relays drain',lambda:state(12,0,12))
        report['final']=probe('verify')
        report['checks']['concurrent_relays_and_ordering']=True
        report['control_after']=lab.control_snapshot()
        assert report['control_before']==report['control_after']
        report['checks']['existing_workload_specs_unchanged']=True
        report['result']='PASS'
    except Exception as exc:
        report['error']=str(exc)
        raise
    finally:
        lab.cleanup()
        report['cleanup']=lab.report.get('cleanup')
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        report['script_hashes']={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
             for pattern in ['worker/*.py','portfolio/metrics.py','alembic/versions/0009*.py','scripts/outbox*py']
             for p in ROOT.glob(pattern)}
        save('summary',report)
        print(json.dumps({'result':report['result'],'output':str(output),'cleanup':report['cleanup']}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--context',required=True);p.add_argument('--image',required=True)
    a=p.parse_args();run(a.context,a.image)
