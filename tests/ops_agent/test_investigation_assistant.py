import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops_agent.diagnosis_agent import ModelResponse
from ops_agent.investigation_assistant import RecordedTools,investigate,render_explanation,MAX_CALLS
from ops_agent.runbook_assistant import catalog,prepare,read_json

ROOT=Path(__file__).resolve().parents[2]


def document():
    source=read_json(ROOT/'ops_agent/fixtures/runbook_assistance_v1.json')['cases'][1]['steps'][0]
    source['evidence'][0]['metric']['name']='application_readiness_observation'
    source['evidence'][1]['metric']['name']='postgres_blocking_activity'
    return source


def advice(context):
    claim={'text':'모델이 직접 작성한 설명입니다.','evidence_ids':[context['evidence'][0]['evidence_id']]}
    return {'current_summary':claim,'impact':{**claim,'severity':'UNKNOWN'},'hypotheses':[claim],
        'unknowns':['현재 증거로 원인 확정 불가'],'next_checks':[{**claim,'runbook_id':None}],
        'change':claim,'runbook_assessments':[{'runbook_id':b['id'],'applicability':'NEEDS_MORE_EVIDENCE',
            'rationale':'모델의 적용 판단','conditions':[{'id':c['id'],'status':'UNKNOWN','evidence_ids':[]}
                for c in b['conditions']]} for b in context['runbooks']]}


def call(name,identifier='call1',arguments=None):
    return {'type':'function_call','name':name,'call_id':identifier,'arguments':json.dumps(arguments or {})}


def test_unread_observation_cannot_be_cited_even_if_it_exists_in_bundle():
    registry=RecordedTools(prepare(document(),'서비스 상태 조사','synthetic'),catalog())
    registry.call('read_service_health',{})
    registry.call('search_runbooks',{'query':'database worker'})
    result=advice(registry.context)
    result['hypotheses'][0]={**result['hypotheses'][0],
        'evidence_ids':[registry.available['evidence'][1]['evidence_id']]}
    with pytest.raises(ValueError,match='unavailable evidence'):
        registry.validate(json.dumps(result))


@pytest.mark.parametrize('name,arguments',[
    ('run_sql',{'query':'DROP TABLE messages'}),('read_service_health',{'url':'https://example.com'}),
    ('search_runbooks',{'query':'x'*301}),('search_runbooks',{'query':'   '})])
def test_only_fixed_reads_and_bounded_search_allowed(name,arguments):
    registry=RecordedTools(prepare(document(),'조사','synthetic'),catalog())
    with pytest.raises(ValueError): registry.call(name,arguments)
    assert not registry.trace


def test_missing_read_does_not_become_normal_evidence_and_repeats_rejected():
    registry=RecordedTools(prepare(document(),'조사','synthetic'),catalog())
    result=registry.call('read_kafka_lag',{})
    assert result['status']=='UNAVAILABLE' and result['evidence']==[]
    with pytest.raises(ValueError,match='repeated'): registry.call('read_kafka_lag',{})


def test_model_selects_reads_and_search_and_explanation_preserves_model_text(tmp_path):
    requests=[]
    def create(payload):
        requests.append(json.loads(json.dumps(payload)))
        if len(requests)==1:
            initial=json.loads(payload['input'][1]['content'])
            assert 'evidence' not in initial and 'runbooks' not in initial
            return ModelResponse('first',[call('read_service_health'),
                call('search_runbooks','call2',{'query':'database'})],None,10,10,20)
        outputs=[json.loads(i['output']) for i in payload['input'] if i.get('type')=='function_call_output']
        context={'evidence':outputs[0]['evidence'],'runbooks':outputs[1]['runbooks']}
        return ModelResponse('second',[],json.dumps(advice(context),ensure_ascii=False),20,20,40)
    result=investigate(document(),'서비스 상태 조사','synthetic',tmp_path/'result',
        client=SimpleNamespace(create=create),model='fake-model')
    assert result['model_calls']==2 and result['explanation_author']=='model'
    assert [t['tool'] for t in result['tool_trace']]==['read_service_health','search_runbooks']
    assert len(result['input']['evidence'])==1
    rendered=(tmp_path/'result/explanation.txt').read_text(encoding='utf-8')
    assert '모델이 직접 작성한 설명입니다.' in rendered and '런북 대조 · 모델 판단' in rendered
    assert rendered==render_explanation(result)


def test_no_automatic_fallback_explanation_when_model_skips_investigation(tmp_path):
    context=prepare(document(),'조사','synthetic')
    client=SimpleNamespace(create=lambda _:ModelResponse('x',[],json.dumps(advice(context)),1,1,2))
    with pytest.raises(ValueError,match='read observations'):
        investigate(document(),'조사','synthetic',tmp_path/'failed',client=client,model='fake')
    assert (tmp_path/'failed/failure.json').exists()
    assert not (tmp_path/'failed/record.json').exists()
    assert not (tmp_path/'failed/explanation.txt').exists()


def test_budget_exhaustion_preserves_failure_without_model_repair(tmp_path):
    names=['read_service_health','read_worker_state','read_kafka_lag','read_database_activity','read_request_states']
    calls=[]
    def create(payload):
        i=len(calls);calls.append(payload)
        return ModelResponse(str(i),[call(names[i],str(i))],None,1,1,2)
    with pytest.raises(ValueError,match='budget'):
        investigate(document(),'조사','synthetic',tmp_path/'budget',client=SimpleNamespace(create=create),model='fake')
    assert len(calls)==MAX_CALLS
    assert not (tmp_path/'budget/record.json').exists()


def test_offline_does_not_claim_model_selected_a_tool(tmp_path):
    result=investigate(document(),'조사','synthetic',tmp_path/'offline')
    assert result['status']=='PREPARED'
    assert not (tmp_path/'offline/trace.json').exists()
    assert not (tmp_path/'offline/explanation.txt').exists()
