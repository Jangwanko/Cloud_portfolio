import http from "k6/http";
import { check, sleep } from "k6";
import exec from "k6/execution";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1";
const HOST_HEADER = __ENV.HOST_HEADER || "localhost";
const STREAM_COUNT = Number(__ENV.K6_STREAM_COUNT || "64");
const PRE_ALLOCATED_VUS = Number(__ENV.K6_PRE_ALLOCATED_VUS || "100");
const MAX_VUS = Number(__ENV.K6_MAX_VUS || "400");
const PHASES = JSON.parse(__ENV.K6_PHASES_JSON || "[]");

if (!Array.isArray(PHASES) || PHASES.length === 0) {
  throw new Error("K6_PHASES_JSON must contain at least one phase");
}
if (!Number.isInteger(STREAM_COUNT) || STREAM_COUNT < 2 || STREAM_COUNT > 1000) {
  throw new Error("K6_STREAM_COUNT must be an integer from 2 to 1000");
}

const acceptedByPhase = {};
const failedByPhase = {};
const scenarioPhase = {};

function metricSuffix(phaseId) {
  return phaseId.toLowerCase().replace(/[^a-z0-9_]/g, "_");
}

function buildScenarios() {
  const scenarios = {};
  let offsetSeconds = 0;
  for (const phase of PHASES) {
    if (
      typeof phase.phase_id !== "string" ||
      typeof phase.profile !== "string" ||
      !Number.isInteger(phase.target_rate) ||
      phase.target_rate < 0 ||
      !Number.isInteger(phase.duration_seconds) ||
      phase.duration_seconds < 1
    ) {
      throw new Error(`invalid phase contract: ${JSON.stringify(phase)}`);
    }
    const scenarioName = `arrival_${metricSuffix(phase.phase_id)}`;
    acceptedByPhase[scenarioName] = new Counter(`${scenarioName}_accepted_202`);
    failedByPhase[scenarioName] = new Counter(`${scenarioName}_failed`);
    scenarioPhase[scenarioName] = phase;
    if (phase.target_rate > 0) {
      scenarios[scenarioName] = {
        executor: "constant-arrival-rate",
        rate: phase.target_rate,
        timeUnit: "1s",
        duration: `${phase.duration_seconds}s`,
        preAllocatedVUs: PRE_ALLOCATED_VUS,
        maxVUs: MAX_VUS,
        startTime: `${offsetSeconds}s`,
        gracefulStop: "0s",
        exec: "eventFlow",
      };
    }
    offsetSeconds += phase.duration_seconds;
  }
  scenarios.phase_clock = {
    executor: "constant-vus",
    vus: 1,
    duration: `${offsetSeconds}s`,
    gracefulStop: "0s",
    exec: "phaseClock",
  };
  return scenarios;
}

export const options = {
  scenarios: buildScenarios(),
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

function postJsonWithRetry(url, payload, params, expectedStatus, label) {
  let response;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    response = http.post(url, JSON.stringify(payload), params);
    if (response.status === expectedStatus) return response;
    if (attempt < 5) sleep(1);
  }
  check(response, { [`${label} (${expectedStatus})`]: (item) => item.status === expectedStatus });
  return response;
}

export function setup() {
  const suffix = Date.now();
  const password = "Password123!";
  const headers = { "Content-Type": "application/json", Host: HOST_HEADER };
  const userAResponse = postJsonWithRetry(
    `${BASE_URL}/v1/users`,
    { username: `phase4_user_a_${suffix}`, password },
    { headers },
    200,
    "create user a",
  );
  const userBResponse = postJsonWithRetry(
    `${BASE_URL}/v1/users`,
    { username: `phase4_user_b_${suffix}`, password },
    { headers },
    200,
    "create user b",
  );
  const userA = JSON.parse(userAResponse.body);
  const userB = JSON.parse(userBResponse.body);
  const loginResponse = postJsonWithRetry(
    `${BASE_URL}/v1/auth/login`,
    { username: `phase4_user_a_${suffix}`, password },
    { headers },
    200,
    "login user a",
  );
  const token = JSON.parse(loginResponse.body).access_token;
  const authHeaders = {
    "Content-Type": "application/json",
    Host: HOST_HEADER,
    Authorization: `Bearer ${token}`,
  };
  const streamIds = [];
  for (let index = 0; index < STREAM_COUNT; index += 1) {
    const response = postJsonWithRetry(
      `${BASE_URL}/v1/streams`,
      {
        name: `phase4-stream-${suffix}-${index}`,
        member_ids: [userA.id, userB.id],
      },
      { headers: authHeaders },
      200,
      `create stream ${index}`,
    );
    streamIds.push(JSON.parse(response.body).id);
  }
  console.log(`PHASE4_SETUP_COMPLETE=${Date.now()}`);
  return { streamIds, token };
}

export function phaseClock() {
  sleep(1);
}

export function eventFlow(data) {
  const scenarioName = exec.scenario.name;
  const phase = scenarioPhase[scenarioName];
  if (!phase) throw new Error(`unknown arrival-rate scenario: ${scenarioName}`);
  const headers = {
    "Content-Type": "application/json",
    Host: HOST_HEADER,
    Authorization: `Bearer ${data.token}`,
  };
  const iteration = exec.scenario.iterationInTest;
  const streamIndex = iteration % data.streamIds.length;
  const payload = JSON.stringify({
    event_type: "portfolio.recovery.calibration",
    payload: {
      message: `phase4 event scenario=${scenarioName} iteration=${iteration}`,
      iteration,
    },
    metadata: {
      calibration_phase: phase.profile,
      phase_id: phase.phase_id,
      target_arrival_rate: phase.target_rate,
      stream_count: STREAM_COUNT,
    },
  });
  const response = http.post(
    `${BASE_URL}/v2/streams/${data.streamIds[streamIndex]}/events`,
    payload,
    { headers },
  );
  if (response.status === 202) acceptedByPhase[scenarioName].add(1);
  else failedByPhase[scenarioName].add(1);
  check(response, { "event request accepted (202)": (item) => item.status === 202 });
}

function metricCount(data, name) {
  return Number(data.metrics?.[name]?.values?.count || 0);
}

export function handleSummary(data) {
  const phases = PHASES.map((phase) => {
    const scenarioName = `arrival_${metricSuffix(phase.phase_id)}`;
    const accepted = metricCount(data, `${scenarioName}_accepted_202`);
    const failed = metricCount(data, `${scenarioName}_failed`);
    return {
      phase_id: phase.phase_id,
      profile: phase.profile,
      target_rate: phase.target_rate,
      duration_seconds: phase.duration_seconds,
      accepted_202: accepted,
      failed,
      http_accepted_rate_per_second:
        phase.duration_seconds > 0 ? accepted / phase.duration_seconds : null,
    };
  });
  const summary = {
    schema_version: "ops.recovery-arrival-workload.v1",
    executor: "constant-arrival-rate",
    stream_count: STREAM_COUNT,
    pre_allocated_vus: PRE_ALLOCATED_VUS,
    max_vus: MAX_VUS,
    phases,
    dropped_iterations: metricCount(data, "dropped_iterations"),
    http_requests: metricCount(data, "http_reqs"),
    checks_rate: Number(data.metrics?.checks?.values?.rate || 0),
  };
  return { stdout: `PHASE4_K6_SUMMARY=${JSON.stringify(summary)}\n` };
}
