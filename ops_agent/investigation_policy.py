"""Bounded, code-owned investigation obligations; never root-cause decisions."""

VERSION = "ops.investigation-policy.v1"


def build_policy(context, facts):
    def single(metric):
        items = [e for e in context['evidence'] if e['metric']['name'] == metric]
        if len(items) != 1:
            return None
        item = items[0]
        cov = item['coverage']
        return item if (item['usable'] and cov['complete'] is True
                        and not cov['missing_items'] and not cov['extra_items']
                        and (cov['expected_count'] is None or cov['expected_count'] == cov['observed_count'])
                        and set(cov['expected_items']) == set(cov['observed_items'])) else None

    worker = {'status': 'UNKNOWN', 'value': None, 'evidence_ids': []}
    item = single('kubernetes_worker_deployment_observation')
    if item is not None:
        value = item['metric']['value']
        if (isinstance(value, dict) and all(type(value.get(k)) is int and value[k] >= 0
                for k in ('desired_replicas', 'ready_replicas'))):
            worker = {'status': 'KNOWN', 'value': {k: value[k] for k in ('desired_replicas', 'ready_replicas')},
                      'evidence_ids': [item['evidence_id']]}
    # Only an explicit typed primary reachability failure is supported here.
    # Generic HTTP 503, healthy readiness and absent waits cannot prove DB health.
    primary_failure_ids = []
    item = single('application_readiness_observation')
    if item is not None:
        try:
            if item['metric']['value']['body']['postgres']['primary_reachable'] is False:
                primary_failure_ids = [item['evidence_id']]
        except (KeyError, TypeError):
            pass
    waits = facts['facts']['database_waits']
    blocked = waits['status'] == 'KNOWN' and bool(waits['value']['blocking_edges'])
    db_ids = sorted(set((waits['evidence_ids'] if blocked else []) + primary_failure_ids))
    constraints = []
    if any(b['id'] == 'RB-PG-HEALTH' for b in context['runbooks']):
        constraints.append({'runbook_id': 'RB-PG-HEALTH', 'condition_id': 'db_failure',
            'time_scope': 'CURRENT', 'status': 'MET' if db_ids else 'UNKNOWN',
            'applicability': 'APPLICABLE' if db_ids else 'NEEDS_MORE_EVIDENCE',
            'evidence_ids': db_ids,
            'basis': 'CURRENT_BLOCKING_OR_PRIMARY_UNREACHABLE' if db_ids else 'NO_SUPPORTED_POSITIVE_DB_SIGNAL',
            'boundary': 'Current investigation relevance only. Not request causality, global DB health or recovery. Historical explanation belongs in updates.'})
    targets = []
    requests = facts['facts']['requests']
    if requests['status'] == 'KNOWN' and requests['value']['unobservable_ids']:
        ids = requests['value']['unobservable_ids']
        targets.append({'id': 'check:status-read', 'request_ids': ids,
            'status_http': {i: requests['value']['per_request'][i]['status_http'] for i in ids},
            'evidence_ids': requests['evidence_ids'],
            'purpose': 'Explain read-only checks for each unavailable status read. For 401/403 check authentication/authorization/visibility; other failures require their own transport/service explanation. Append acknowledgement stays known.'})
    if worker['status'] == 'KNOWN' and worker['value']['desired_replicas'] > 0 and worker['value']['ready_replicas'] == 0:
        targets.append({'id': 'check:worker-availability', 'request_ids': [],
            'evidence_ids': worker['evidence_ids'],
            'purpose': 'Investigate observed desired>0/ready=0 using read-only pod readiness, scheduling/startup events and logs. Explain possible relevance, not confirmed causality or scaling permission.'})
    if blocked:
        targets.append({'id': 'check:db-blocking', 'request_ids': [],
            'evidence_ids': waits['evidence_ids'],
            'purpose': 'Read waiter/blocker transaction/query details and correlate with Worker persistence/request IDs. A blocking edge alone does not establish the cause of these requests.'})
    return {'version': VERSION, 'worker_observation': worker,
            'current_runbook_constraints': constraints, 'required_checks': targets,
            'boundary': 'Bounded code policy, not LLM reasoning accuracy. Target coverage checks IDs and evidence, not whether prose actually performs the investigation.'}


def validate_policy(result, policy):
    for fixed in policy['current_runbook_constraints']:
        assessment = next(a for a in result['runbook_assessments'] if a['runbook_id'] == fixed['runbook_id'])
        condition = next(c for c in assessment['conditions'] if c['id'] == fixed['condition_id'])
        if (assessment['time_scope'] != fixed['time_scope'] or assessment['applicability'] != fixed['applicability']
                or condition['status'] != fixed['status'] or not set(fixed['evidence_ids']) <= set(condition['evidence_ids'])):
            raise ValueError('model contradicts code-owned investigation relevance')
    targets = {t['id']: t for t in policy['required_checks']}
    covered = set()
    for check in result['next_checks']:
        ids = check['target_ids']
        if len(ids) != len(set(ids)) or not set(ids) <= targets.keys():
            raise ValueError('unknown or duplicate investigation target')
        for identifier in ids:
            if not set(targets[identifier]['evidence_ids']) <= set(check['evidence_ids']):
                raise ValueError('investigation target lacks its observation citation')
        covered.update(ids)
    if covered != targets.keys():
        raise ValueError('required investigation target omitted')
