"""Local row-lock fault: bounded server-side lifetime, real request evidence, no reset.

The model sees recorded observations only, after the fault has been released.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops_agent.calibration import summarize_bundle
from ops_agent.controller import collect_bundle
from ops_agent.models import EvidenceBundle
from ops_agent.policies import load_policy
from ops_agent.runbook_assistant import read_json, save, run as assist
from scripts.run_local_burst import LocalClient, now
from scripts.run_worker_fault_diagnosis import kube

PG_PREFIX = "messaging-postgresql-ha-postgresql-"
PG_SHELL = '''set -eu
set +x
if [ -n "${POSTGRES_POSTGRES_PASSWORD_FILE:-}" ] && [ -r "$POSTGRES_POSTGRES_PASSWORD_FILE" ]; then
  PGPASSWORD="$(cat "$POSTGRES_POSTGRES_PASSWORD_FILE")"
elif [ -r /opt/bitnami/postgresql/secrets/postgres-password ]; then
  PGPASSWORD="$(cat /opt/bitnami/postgresql/secrets/postgres-password)"
elif [ -n "${POSTGRES_POSTGRES_PASSWORD:-}" ]; then
  PGPASSWORD="$POSTGRES_POSTGRES_PASSWORD"
else
  exit 41
fi
export PGPASSWORD
exec /opt/bitnami/postgresql/bin/psql -X -qAt -v ON_ERROR_STOP=1 -U postgres -d portfolio -f -
'''

# Generic wait graph. No injection session name, sleeping SQL or private plan is
# projected to the model. The relation and blocking PID are actual DB observations.
WAIT_SQL = """WITH waiting AS (
 SELECT pid, state, wait_event_type, wait_event, pg_blocking_pids(pid) AS blocking_pids,
 extract(epoch from clock_timestamp()-xact_start)::float AS transaction_age_seconds,
 CASE WHEN query LIKE '%INSERT INTO room_sequences%' THEN 'INSERT INTO room_sequences ... ON CONFLICT DO UPDATE'
      ELSE 'other statement (text omitted)' END AS statement_kind
 FROM pg_stat_activity WHERE datname=current_database() AND cardinality(pg_blocking_pids(pid))>0
), blocker_ids AS (SELECT DISTINCT unnest(blocking_pids) AS pid FROM waiting)
SELECT json_build_object('captured_at',clock_timestamp(),
 'waiting',COALESCE((SELECT json_agg(w) FROM waiting w),'[]'::json),
 'blockers',COALESCE((SELECT json_agg(b) FROM (
   SELECT a.pid,a.state,extract(epoch from clock_timestamp()-a.xact_start)::float AS transaction_age_seconds
   FROM pg_stat_activity a JOIN blocker_ids i USING(pid)) b),'[]'::json),
 'locks',COALESCE((SELECT json_agg(l) FROM (
   SELECT pid,locktype,mode,granted,relation::regclass::text AS relation
   FROM pg_locks WHERE pid IN (SELECT pid FROM waiting UNION SELECT pid FROM blocker_ids)) l),'[]'::json));"""

METRICS = {"application_readiness_observation", "application_kafka_runtime_observation",
    "application_postgres_runtime_observation", "application_ops_summary_observation",
    "kubernetes_worker_deployment_observation", "kubernetes_worker_pod_observations",
    "kubernetes_worker_scaled_object_observation", "kafka_consumergroup_lag",
    "exact_postgres_commit_insert_rate", "postgres_transaction_commit_latency"}


def sql_command(pod):
    if not re.fullmatch(PG_PREFIX + r"[012]", pod):
        raise ValueError("only fixed local PostgreSQL pods allowed")
    return [str(ROOT / "tools/kubectl.exe"), "--context", "kind-messaging-ha",
            "-n", "messaging-app", "exec", "-i", pod, "--", "sh", "-c", PG_SHELL]


def sql(pod, query):
    result = subprocess.run(sql_command(pod), input="SET statement_timeout='5s';\n"+query,
        capture_output=True, text=True, encoding="utf-8", timeout=12)
    if result.returncode:
        raise RuntimeError("local database query failed")
    return json.loads(result.stdout)


def lock_sql(stream_id, session_name, seconds=50):
    if type(stream_id) is not int or stream_id <= 0 or not re.fullmatch(r"ops_session_[a-f0-9]{12}",session_name):
        raise ValueError("invalid lock identity")
    if not 1 <= seconds <= 50:
        raise ValueError("row lock duration must be 1-50 seconds")
    return f"""SET application_name='{session_name}';
SET statement_timeout='55s';
SET lock_timeout='2s';
SET idle_in_transaction_session_timeout='60s';
BEGIN;
SELECT room_id FROM public.room_sequences WHERE room_id={stream_id} FOR UPDATE;
SELECT pg_sleep({seconds});
ROLLBACK;
"""


def persisted(rows, expected):
    if not expected or len(expected)!=len(set(expected)) or len(rows)!=len(expected):
        return False
    if {r['request_id'] for r in rows} != set(expected):
        return False
    event_ids = [r.get('body',{}).get('event_id') for r in rows]
    return (all(r['http_status']==200 and r.get('body',{}).get('status')=='persisted'
                and r['body'].get('request_id',r['request_id'])==r['request_id'] for r in rows)
            and all(event_ids) and len(set(event_ids))==len(expected))


def supplemental(name, value, at, end, path):
    age = (datetime.fromisoformat(end.replace('Z','+00:00'))-datetime.fromisoformat(at)).total_seconds()
    return {"evidence_id":"local."+name,"status":"OK","source":"local_observation",
        "tool_id":"local.fixed_read.v1","source_timestamp":at,"collected_at":at,
        "freshness":{"status":"FRESH" if age<=60 else "STALE","age_seconds":age,"max_age_seconds":60,
                     "basis":"collector_time_local_response"},
        "metric":{"name":name,"value":value},"labels":{},"coverage":{"complete":True},
        "semantic":{"type":"recorded_local_observation","notes":"Recorded at stated time. Request acceptance is distinct from persistence. No future results included."},
        "raw_ref":path.resolve().relative_to(ROOT).as_posix(),"raw_sha256":hashlib.sha256(path.read_bytes()).hexdigest()}


def preflight():
    primaries=[]
    for i in range(3):
        pod=PG_PREFIX+str(i)
        state=sql(pod,"SELECT json_build_object('primary',NOT pg_is_in_recovery(),'version',current_setting('server_version'),'sequence_table',to_regclass('public.room_sequences')::text);")
        if state['primary']: primaries.append((pod,state))
    if len(primaries)!=1 or primaries[0][1]['sequence_table'] is None:
        raise ValueError("one primary and sequence table required")
    return primaries[0]


def run(directory, live=False):
    directory=directory.resolve()
    directory.mkdir(parents=True,exist_ok=False)
    primary, pg = preflight()
    session="ops_session_"+secrets.token_hex(6)
    incident="local-observation-"+secrets.token_hex(6)
    plan={"at":now(),"incident_id":incident,"primary":primary,"database":pg,
        "fault":"50-second FOR UPDATE lock on one newly-created stream sequence row",
        "rollback":"server-side pg_sleep(50) then ROLLBACK; statement_timeout 55s, idle transaction timeout 60s",
        "lock_acquisition_timeout_seconds":2,"max_events":21,"max_model_calls":3 if live else 0,
        "model_mode":"post-capture sequential replay; no private fault plan or future observations",
        "evaluation_before_run":["202 with unpersisted known IDs despite ready health",
            "Actual DB waiter linked to blocking transaction", "Do not infer DB outage or overload from lag alone",
            "Root cause moves from uncertain to lock contention when wait graph supplied",
            "All accepted IDs persist and blocker session disappears after bounded release"],
        "deployment_changes":False,"data_reset":False,
        "source_sha256":{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in
            ['scripts/run_local_lock_fault.py','scripts/run_local_burst.py','ops_agent/runbook_assistant.py','ops_agent/diagnosis_agent.py']}}
    save(directory/'plan-private.json',plan)
    (directory/'runner-source.py').write_bytes(Path(__file__).read_bytes())
    client=LocalClient()
    policy=load_policy('local-ha')
    baseline=collect_bundle(policy=policy,incident_id=incident,artifact_root=directory/'raw',cluster_context='kind-messaging-ha')
    save(directory/'baseline.json',baseline.model_dump(mode='json'))
    initial=summarize_bundle(baseline)
    if initial['kafka']['total_lag']!=0 or initial['kafka']['anomalies'] or initial['postgres']['readiness_body_status']!='ready':
        raise ValueError('healthy zero-lag baseline required')
    before_pods=kube('get','pods','-l','app.kubernetes.io/component=postgresql','-o','json')
    save(directory/'pg-before.json',before_pods)
    if len(before_pods['items'])!=3 or not all(
        p.get('status',{}).get('containerStatuses') and all(c.get('ready') for c in p['status']['containerStatuses'])
        for p in before_pods['items']):
        raise ValueError('three ready PostgreSQL pods required before injection')
    username,password='probe'+secrets.token_hex(6),secrets.token_urlsafe(24)
    code,user=client.request('POST','/v1/users',{'username':username,'password':password})
    if code not in (200,201): raise ValueError('user setup failed')
    code,login=client.request('POST','/v1/auth/login',{'username':username,'password':password})
    if code!=200: raise ValueError('login failed')
    client.token=login['access_token']
    streams=[]
    for i in range(9):
        code,s=client.request('POST','/v1/streams',{'name':incident+f'-{i}','member_ids':[user['id']]})
        if code not in (200,201): raise ValueError('stream setup failed')
        streams.append(s['id'])
    save(directory/'streams.json',streams)
    accepted=[]
    def send(stream_id,seq):
        code,ack=client.request('POST',f'/v2/streams/{stream_id}/events',
            {'event_type':'portfolio.observation.probe','payload':{'sequence':seq},'metadata':{'scenario':incident}},key=f'{incident}-{seq}')
        if code!=202 or ack.get('schema_version')!=2 or not ack.get('request_id'):
            raise ValueError('event not accepted with v2 request ID')
        row={'sequence':seq,'stream_id':stream_id,'request_id':ack['request_id'],'http_status':code,'at':now()}
        if row['request_id'] in {r['request_id'] for r in accepted}: raise ValueError('duplicate accepted ID')
        accepted.append(row)
        save(directory/'accepted'/f'{seq:03d}.json',row)
    def poll():
        rows=[]
        for item in accepted:
            code,body=client.request('GET','/v2/event-requests/'+item['request_id'])
            rows.append({'request_id':item['request_id'],'http_status':code,'at':now(),
                         'body':{k:body[k] for k in ['request_id','status','event_id','detail'] if k in body}})
        return rows
    send(streams[0],0)
    seed_deadline=time.perf_counter()+30
    while time.perf_counter()<seed_deadline:
        seed=poll()
        if persisted(seed,[accepted[0]['request_id']]): break
        time.sleep(1)
    else: raise ValueError('seed persistence failed; lock not injected')
    save(directory/'seed-persistence.json',seed)
    if sql(primary,f"SELECT count(*) FROM room_sequences WHERE room_id={int(streams[0])};")!=1:
        raise ValueError('exactly one owned sequence row required')
    snapshots=[]
    def capture(label,include_db,model_step=True):
        path=directory/label
        rows=poll()
        request_value={'accepted':list(accepted),'status_polls':rows,
            'note':'404 only means no durable request row yet for this known accepted ID. No data-loss conclusion.'}
        at=now(); save(path/'requests.json',request_value)
        db=sql(primary,WAIT_SQL); db_at=now(); save(path/'database-waits.json',db)
        bundle=collect_bundle(policy=policy,incident_id=incident,artifact_root=path/'raw',cluster_context='kind-messaging-ha')
        full=bundle.model_dump(mode='json'); save(path/'bundle-full.json',full)
        selected=[e for e in full['evidence'] if e['metric']['name'] in METRICS]
        selected.append(supplemental('event_acceptance_and_persistence',request_value,at,full['collection']['completed_at'],path/'requests.json'))
        if include_db:
            selected.append(supplemental('postgres_blocking_activity',db,db_at,full['collection']['completed_at'],path/'database-waits.json'))
        projected={**full,'evidence':selected}
        EvidenceBundle.model_validate(projected)
        save(path/'bundle-model.json',projected)
        save(path/'selection.json',{'original_sha256':hashlib.sha256((path/'bundle-full.json').read_bytes()).hexdigest(),
            'included_metrics':sorted(METRICS),'db_waits_in_model_input':include_db,'supplemental_sources':['requests.json']+(['database-waits.json'] if include_db else [])})
        if model_step: snapshots.append((label,projected))
        print(json.dumps({'phase':label,'lag':summarize_bundle(bundle)['kafka']['total_lag'],
            'accepted':len(accepted),'persisted':sum(r.get('body',{}).get('status')=='persisted' for r in rows),
            'db_waiters':len(db['waiting'])}),flush=True)
        return rows,db,summarize_bundle(bundle)
    stdout=(directory/'lock-session-output.txt').open('x',encoding='utf-8')
    stderr=(directory/'lock-session-error.txt').open('x',encoding='utf-8')
    process=None
    failure=None
    try:
        process=subprocess.Popen(sql_command(primary),stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,
            text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        process.stdin.write(lock_sql(streams[0],session));process.stdin.close()
        deadline=time.perf_counter()+10
        while time.perf_counter()<deadline:
            active=sql(primary,f"SELECT COALESCE(json_agg(pid),'[]'::json) FROM pg_stat_activity WHERE application_name='{session}' AND wait_event='PgSleep';")
            if len(active)==1: break
            if process.poll() is not None: raise ValueError('lock session exited before confirmation')
            time.sleep(.3)
        else: raise ValueError('lock acquisition not confirmed')
        save(directory/'injection-private.json',{'confirmed_at':now(),'pid':active[0],'session':session,'stream':streams[0]})
        for seq in range(1,21):
            send(streams[0] if seq<=12 else streams[seq-12],seq)
            time.sleep(.15)
        time.sleep(3)
        rows,db,_=capture('01-symptoms',False)
        target_ids={r['request_id'] for r in accepted if r['stream_id']==streams[0] and r['sequence']>0}
        from ops_agent.experiment_validation import require_stalled_requests
        require_stalled_requests(target_ids,[r for r in rows if r['request_id'] in target_ids])
        if not any(active[0] in w['blocking_pids'] for w in db['waiting']):
            raise ValueError('intended blocker not linked to real waiter')
        time.sleep(5)
        capture('02-investigation',True)
    except Exception as exc:
        failure=type(exc).__name__
        save(directory/'observation-failure.json',{'at':now(),'error_type':failure})
    finally:
        if process is not None:
            # Independent server timeouts also release the lock if this runner exits.
            try: process.wait(timeout=70)
            except subprocess.TimeoutExpired:
                process.terminate();process.wait(timeout=5)
        stdout.close();stderr.close()
    remaining=sql(primary,f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{session}';")
    save(directory/'release.json',{'at':now(),'remaining_owned_sessions':remaining,
        'process_exit_code':process.returncode if process else None})
    if remaining: raise ValueError('owned session still exists; recovery not claimed')
    deadline=time.perf_counter()+90
    index=0
    while time.perf_counter()<deadline:
        rows=poll();save(directory/'recovery-polls'/f'{index:03d}.json',rows);index+=1
        if persisted(rows,[r['request_id'] for r in accepted]): break
        time.sleep(2)
    else: raise ValueError('not every accepted request persisted')
    stable=0
    for check in range(9):
        _,_,state=capture(f'recovery-check-{check:02d}',True,False)
        good=(state['kafka']['total_lag']==0 and not state['kafka']['anomalies']
              and state['postgres']['readiness_body_status']=='ready')
        stable=stable+1 if good else 0
        if stable>=3: break
        time.sleep(5)
    rows,db,final=capture('03-recovery',True)
    after_pods=kube('get','pods','-l','app.kubernetes.io/component=postgresql','-o','json')
    save(directory/'pg-after.json',after_pods)
    def identities(document):
        return sorted((p['metadata']['name'],p['metadata']['uid'],c['name'],c['restartCount'])
                      for p in document['items'] for c in p.get('status',{}).get('containerStatuses',[]))
    stable_pg=len(before_pods['items'])==len(after_pods['items'])==3 and identities(before_pods)==identities(after_pods)
    observation_ok=(not failure and persisted(rows,[r['request_id'] for r in accepted]) and
        not db['waiting'] and final['postgres']['readiness_body_status']=='ready' and stable_pg
        and stable>=3 and final['kafka']['total_lag']==0 and not final['kafka']['anomalies'])
    save(directory/'observation-summary.json',{'status':'COMPLETE' if observation_ok else 'INCOMPLETE',
        'accepted':len(accepted),'persisted':sum(r.get('body',{}).get('status')=='persisted' for r in rows),
        'all_accepted_verified':persisted(rows,[r['request_id'] for r in accepted]),'pg_uids_and_restarts_unchanged':stable_pg,
        'remaining_owned_sessions':remaining,'final_kafka_lag':final['kafka']['total_lag'],
        'consecutive_healthy_zero_lag_samples':stable,'failure':failure})
    if not observation_ok: return 1
    if live:
        from ops_agent.diagnosis_agent import OpenAIResponsesClient,load_openai_configuration
        key,model=load_openai_configuration(ROOT)
        api=OpenAIResponsesClient(api_key=key,timeout_seconds=45,max_retries=0)
        previous=None
        for label,document in snapshots:
            try:
                previous=assist(document,'현재 증거로 서비스 영향과 원인 후보를 설명하고, 런북 적용 조건과 다음 확인을 제시해줘. 이전 판단이 있다면 변경 근거를 설명해줘.',
                    'recorded',directory/'model'/label,previous=previous,client=api,model=model)
                print(json.dumps({'model_phase':label,'severity':previous['advice']['impact']['severity'],
                    'summary':previous['advice']['current_summary']['text']},ensure_ascii=False),flush=True)
            except Exception as exc:
                save(directory/'model-failure.json',{'phase':label,'error_type':type(exc).__name__,'retry':False})
                return 2
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    if not args.execute:
        print(json.dumps({'preflight':preflight(),'fault_injected':False}));sys.exit(0)
    if args.output_dir is None: parser.error('--execute requires a new --output-dir')
    sys.exit(run(args.output_dir,args.live))
