from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import pytest
from ops_agent.experiment_validation import RecoveryWindow, require_stalled_requests, validate_diagnosis


@pytest.mark.parametrize('code',[401,403,500,503])
def test_query_failure_is_not_stall(code):
    with pytest.raises(ValueError):
        require_stalled_requests(['r'],[{'request_id':'r','http_status':code,'body':{'detail':'unavailable'}}])


def test_stall_requires_exact_coverage_and_valid_pending_state():
    row={'request_id':'r','http_status':404,'body':{'detail':'Request not found'}}
    require_stalled_requests(['r'],[row])
    with pytest.raises(ValueError): require_stalled_requests(['r','s'],[row])
    with pytest.raises(ValueError): require_stalled_requests(['r','r'],[row,row])
    require_stalled_requests(['r'],[dict(row,http_status=200,body={'status':'queued'})])


def test_diagnosis_cannot_cite_unseen_evidence():
    text=json.dumps({'root_cause':'x','evidence_ids':['hidden'],'alternatives':['unknown'],
        'recommended_action':'inspect','verification_steps':['check'],'uncertainty':'uncertain'})
    with pytest.raises(ValueError): validate_diagnosis(text,{'E1'},['get_logs'])


def pods():
    return [{'name':f'pg-{i}','uid':f'uid-{i}','containers':[{'name':'pg','ready':True,'restartCount':0}]} for i in range(3)]


def test_recovery_requires_all_pods_and_resets_on_gap_uid_restart():
    window=RecoveryWindow(['pg-0','pg-1','pg-2'],'pg-0','uid-0',0,seconds=30,max_gap=21)
    start=datetime(2026,11,1,tzinfo=timezone.utc)
    def observe(items, seconds): return window.observe(items,200,{'status':'ready'},(start+timedelta(seconds=seconds)).isoformat())
    assert not observe(pods()[:1],0)['healthy']
    assert observe(pods(),15)['sampled_stable_seconds']==0
    assert not observe(pods(),30)['complete']
    assert observe(pods(),45)['complete']
    assert observe(pods(),90)['sampled_stable_seconds']==0
    changed=pods(); changed[1]['uid']='replacement'
    assert observe(changed,105)['sampled_stable_seconds']==0
    changed[1]['containers'][0]['restartCount']=1
    assert observe(changed,120)['sampled_stable_seconds']==0
    changed[0]['containers'][0]['restartCount']=1
    assert not observe(changed,135)['healthy']


def test_recovery_http_error_and_missing_uid_cannot_pass():
    window=RecoveryWindow(['pg-0','pg-1','pg-2'],'pg-0','uid-0',0)
    broken=pods(); broken[1].pop('uid')
    assert not window.observe(broken,200,{'status':'ready'},'2026-11-01T00:00:00+00:00')['healthy']
    assert not window.observe(pods(),503,{'status':'ready'},'2026-11-01T00:00:15+00:00')['healthy']
