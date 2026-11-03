"""Model-selected observation reads and runbook search, with verbatim explanation.

This adapter reads one saved evidence bundle. It cannot query a live cluster or
execute SQL, shell commands, remediation or arbitrary file/URL reads.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from ops_agent.evaluation_models import canonical_sha256
from ops_agent.runbook_assistant import (
    Advice, IMPORTED_CODE_SHA256, MAX_OUTPUT_TOKENS, PROMPT, VERSION,
    catalog, prepare, retrieve, save, validate_advice, verify_record,
)

MODE = "model_selected_recorded_investigation.v1"
MAX_CALLS = 5
ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = {**IMPORTED_CODE_SHA256,
    "ops_agent/investigation_assistant.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

GROUPS = {
    "read_request_states": {"event_acceptance_and_persistence"},
    "read_service_health": {"application_readiness_observation", "application_kafka_runtime_observation",
                            "application_postgres_runtime_observation"},
    "read_worker_state": {"kubernetes_worker_deployment_observation", "kubernetes_worker_pod_observations",
                           "kubernetes_worker_scaled_object_observation", "application_ops_summary_observation"},
    "read_kafka_lag": {"kafka_consumergroup_lag"},
    "read_database_activity": {"postgres_blocking_activity", "exact_postgres_commit_insert_rate",
                               "postgres_transaction_commit_latency"},
}
DESCRIPTIONS = {
    "read_request_states": "Read observed event acceptance receipts and request persistence status polls from this snapshot.",
    "read_service_health": "Read application readiness and Kafka/PostgreSQL health observations from this snapshot.",
    "read_worker_state": "Read Worker replica/pod/restart/scaling observations and advisory summary from this snapshot.",
    "read_kafka_lag": "Read per-partition Kafka consumer lag time series and coverage from this snapshot.",
    "read_database_activity": "Read available database activity/waiting-session observations and commit telemetry from this snapshot; absent telemetry stays unavailable.",
    "search_runbooks": "Search indexed repository runbook sections by your query and evidence already read. Returns original excerpts and applicability conditions, not a diagnosis.",
}

INVESTIGATION_PROMPT = PROMPT + """
You must select the useful observation tools yourself and search_runbooks before
the final answer. You may request several tools together. Tools return only this
saved snapshot, not current live state. Read each tool at most once. A missing
tool result does not establish normality. Only cite evidence actually returned by
tools and assess exactly the runbooks returned by search_runbooks. Do not invent
evidence IDs, unseen sources or a diagnosis from the tool descriptions alone.
Your final JSON text is shown directly to the operator without a human rewriting
or completing its explanation. Explain why each proposed cause follows from the
observations, how retrieved procedures apply, and what remains uncertain.
There are at most five model calls and six unique tools; the last call must answer.
The current impact concerns the latest observations, not the maximum value in a
historical window. Explain recent impact separately in change. Resolution of an
active cause does not negate its earlier observed existence. Previous advice is
still fallible history, not current proof. Do not call old restarts a new incident
without a demonstrated time relationship.
"""


def tool_definitions():
    tools=[]
    for name, description in DESCRIPTIONS.items():
        parameters={"type":"object","properties":{},"required":[],"additionalProperties":False}
        if name=="search_runbooks":
            parameters["properties"]={"query":{"type":"string","minLength":1,"maxLength":300}}
            parameters["required"]=["query"]
        tools.append({"type":"function","name":name,"description":description,
                      "parameters":parameters,"strict":True})
    return tools


class RecordedTools:
    def __init__(self, context, entries):
        self.available=context
        self.entries=entries
        self.context={**context,"evidence":[],"runbooks":[]}
        self.called=set()
        self.trace=[]

    def call(self, name, arguments):
        if name not in DESCRIPTIONS or name in self.called:
            raise ValueError("unknown or repeated read tool")
        if not isinstance(arguments,dict):
            raise ValueError("tool arguments must be an object")
        if name=='search_runbooks':
            if set(arguments)!={'query'} or not isinstance(arguments['query'],str) or not 1<=len(arguments['query'].strip())<=300:
                raise ValueError('bounded runbook query required')
            books=retrieve(arguments['query'],self.context['evidence'],self.entries)
            self.context['runbooks']=books
            result={"runbooks":books,"warning":"Original source excerpts; applicability is for you to assess against observed evidence."}
        else:
            if arguments: raise ValueError('read tools accept no arguments')
            evidence=[e for e in self.available['evidence'] if e['metric']['name'] in GROUPS[name]]
            self.context['evidence'].extend(evidence)
            result={"observed_at":self.context['observed_at'],"evidence":evidence,
                    "status":"AVAILABLE" if evidence else "UNAVAILABLE",
                    "warning":"Recorded observations only; not a live read."}
        self.called.add(name)
        self.trace.append({"tool":name,"arguments":arguments,"result_sha256":canonical_sha256(result),
            "evidence_ids":[e['evidence_id'] for e in result.get('evidence',[])],
            "runbook_ids":[b['id'] for b in result.get('runbooks',[])]})
        return result

    def validate(self, text):
        if 'search_runbooks' not in self.called or not self.context['evidence']:
            raise ValueError('read observations and search runbooks before explaining')
        return validate_advice(text,self.context)


def render_explanation(record):
    """Only labels and provenance are authored here; explanation text is verbatim."""
    verify_record(record)
    advice=record['advice']
    lines=[f"설명 작성: {record['model']}",
        f"관측 시각: {record['input']['observed_at']} ({record['input']['provenance']})",
        "모델 원문을 항목별로 표시합니다. 코드의 형식·인용 검사는 내용의 정확성을 보증하지 않습니다.",""]
    def claim(label, value):
        lines.extend([label,value['text'],'근거: '+', '.join(value['evidence_ids']),""])
    claim('현재 상태',advice['current_summary'])
    claim('현재 영향: '+advice['impact']['severity'],advice['impact'])
    for item in advice['hypotheses']: claim('원인 후보 · 모델 해석',item)
    books={b['id']:b for b in record['input']['runbooks']}
    for item in advice['runbook_assessments']:
        book=books[item['runbook_id']]
        lines.extend([f"런북 대조 · 모델 판단: {item['runbook_id']} / {item['applicability']}",item['rationale']])
        for condition in item['conditions']:
            lines.append(f"조건 {condition['id']}: {condition['status']} / 근거: "+', '.join(condition['evidence_ids']))
        lines.extend([f"원문 출처: {book['source_path']} / {book['source_heading']} / sha256={book['source_sha256']}",""])
    if not books: lines.extend(['검색된 런북 없음',""])
    for item in advice['next_checks']: claim('다음 확인 · 모델 제안',item)
    for item in advice['unknowns']: lines.extend(['미확인 사항',item,""])
    claim('이전 판단과의 변화',advice['change'])
    lines.append('관측 근거 색인')
    for item in record['input']['evidence']:
        lines.append(f"{item['evidence_id']} | {item['metric']['name']} | {item['source_timestamp']} | {item['status']} | usable={item['usable']}")
    return '\n'.join(lines)+'\n'


def investigate(document, question, provenance, directory, previous=None, client=None, model=None):
    available=prepare(document,question,provenance,previous)
    # Preparation can inspect full input for bounds, but no observations or
    # pre-retrieved books are sent to the model before it requests their tools.
    available['runbooks']=[]
    entries=catalog()
    registry=RecordedTools(available,entries)
    directory.mkdir(parents=True,exist_ok=False)
    save(directory/'available-input.json',available)
    save(directory/'catalog-snapshot.json',entries)
    save(directory/'plan.json',{'mode':MODE,'at':datetime.now(timezone.utc).isoformat(),'model':model,
        'max_api_calls':MAX_CALLS,'max_unique_tool_calls':len(DESCRIPTIONS),'max_output_tokens_per_call':MAX_OUTPUT_TOKENS,
        'automatic_retry':False,'explanation_author':'model','live_source_queries':False,
        'imported_code_sha256':SOURCE_SHA256,'prompt_sha256':canonical_sha256(INVESTIGATION_PROMPT),
        'tool_definitions_sha256':canonical_sha256(tool_definitions()),'catalog_sha256':canonical_sha256(entries)})
    if client is None:
        return {'status':'PREPARED','mode':MODE,'tool_names':list(DESCRIPTIONS)}
    conversation=[{'role':'developer','content':INVESTIGATION_PROMPT+'\nFinal JSON schema:\n'+json.dumps(Advice.model_json_schema())},
        {'role':'user','content':json.dumps({k:v for k,v in registry.context.items() if k not in {'evidence','runbooks'}},ensure_ascii=False)}]
    usage={'input_tokens':0,'output_tokens':0}
    call_ids=set()
    try:
        for index in range(MAX_CALLS):
            request={'model':model,'store':False,'max_output_tokens':MAX_OUTPUT_TOKENS,'input':conversation,
                'tools':tool_definitions(),'tool_choice':'required' if index==0 else 'none' if index==MAX_CALLS-1 else 'auto'}
            save(directory/'calls'/f'{index:02d}-request.json',request)
            response=client.create(request)
            save(directory/'calls'/f'{index:02d}-response.json',asdict(response))
            usage['input_tokens']+=response.input_tokens;usage['output_tokens']+=response.output_tokens
            calls=[item for item in response.output if item.get('type')=='function_call']
            if calls:
                if index==MAX_CALLS-1 or len(registry.trace)+len(calls)>len(DESCRIPTIONS):
                    raise ValueError('investigation budget exceeded')
                conversation.extend(response.output)
                for position,call in enumerate(calls):
                    if not isinstance(call.get('call_id'),str) or not call['call_id'] or call['call_id'] in call_ids:
                        raise ValueError('invalid or duplicate tool call ID')
                    call_ids.add(call['call_id'])
                    result=registry.call(call['name'],json.loads(call['arguments']))
                    save(directory/'calls'/f'{index:02d}-{position:02d}-tool.json',
                        {'call_id':call['call_id'],'name':call['name'],'arguments':json.loads(call['arguments']),'result':result})
                    conversation.append({'type':'function_call_output','call_id':call['call_id'],'output':json.dumps(result,ensure_ascii=False)})
                continue
            advice=registry.validate(response.output_text)
            changes={'previous_record_sha256':previous['record_sha256'] if previous else None,
                'severity_changed':previous is not None and previous['advice']['impact']['severity']!=advice['impact']['severity'],
                'applicability_changes':[]}
            old={a['runbook_id']:a['applicability'] for a in previous['advice']['runbook_assessments']} if previous else {}
            new={a['runbook_id']:a['applicability'] for a in advice['runbook_assessments']}
            changes['applicability_changes']=[{'runbook_id':k,'before':old.get(k),'after':new.get(k)}
                for k in sorted(old.keys()|new.keys()) if old.get(k)!=new.get(k)]
            record={'version':VERSION,'status':'RECORDED','mode':MODE,'input':registry.context,'model':model,
                'advice':advice,'changes':changes,'usage':usage,'model_calls':index+1,'tool_trace':registry.trace,
                'explanation_author':'model','validation':'acquired-source/structure only; semantic review required'}
            record['record_sha256']=canonical_sha256(record)
            save(directory/'record.json',record)
            with (directory/'explanation.txt').open('x',encoding='utf-8') as handle:
                handle.write(render_explanation(record))
            save(directory/'trace.json',registry.trace)
            return record
        raise ValueError('no final model explanation')
    except Exception as exc:
        save(directory/'failure.json',{'status':'FAILED','error_type':type(exc).__name__,'retry':False,
            'completed_tool_trace':registry.trace,'usage':usage,'explanation_author':None})
        raise
