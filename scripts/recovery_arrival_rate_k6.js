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

function buildDiagnosticThresholds() {
  const thresholds = {};
  for (const phase of PHASES) {
    if (phase.target_rate <= 0) continue;
    const scenarioName = `arrival_${metricSuffix(phase.phase_id)}`;
    for (const metricName of [
      "dropped_iterations",
      "iterations",
      "iteration_duration",
      "http_req_duration",
      "http_req_failed",
    ]) {
      const expression =
        metricName === "dropped_iterations" || metricName === "iterations"
          ? "count>=0"
          : metricName === "http_req_failed"
            ? "rate>=0"
            : "max>=0";
      thresholds[`${metricName}{scenario:${scenarioName}}`] = [expression];
    }
  }
  return thresholds;
}

export const options = {
  scenarios: buildScenarios(),
  thresholds: buildDiagnosticThresholds(),
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
  return { streamIds, token, setupEpochMs: Date.now() };
}

function phaseAtElapsed(elapsedSeconds) {
  let offsetSeconds = 0;
  for (const phase of PHASES) {
    if (elapsedSeconds < offsetSeconds + phase.duration_seconds) return phase;
    offsetSeconds += phase.duration_seconds;
  }
  return PHASES[PHASES.length - 1];
}

export function phaseClock(data) {
  const elapsedSeconds = Math.max(0, (Date.now() - data.setupEpochMs) / 1000);
  const phase = phaseAtElapsed(elapsedSeconds);
  console.log(
    [
      "PHASE4_LOADGEN_SAMPLE",
      Date.now(),
      elapsedSeconds.toFixed(3),
      phase.phase_id,
      phase.profile,
      exec.instance.vusActive,
      exec.instance.vusInitialized,
      exec.instance.iterationsCompleted,
      exec.instance.iterationsInterrupted,
    ].join("|"),
  );
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
  const value = data.metrics?.[name]?.values?.count;
  return value === undefined || value === null ? null : Number(value);
}

function metricRate(data, name) {
  const value = data.metrics?.[name]?.values?.rate;
  return value === undefined || value === null ? null : Number(value);
}

function metricGauge(data, name) {
  const values = data.metrics?.[name]?.values;
  if (!values) return null;
  return {
    value: values.value === undefined ? null : Number(values.value),
    min: values.min === undefined ? null : Number(values.min),
    max: values.max === undefined ? null : Number(values.max),
  };
}

function metricTrend(data, name) {
  const values = data.metrics?.[name]?.values;
  if (!values) return null;
  return {
    avg: values.avg === undefined ? null : Number(values.avg),
    min: values.min === undefined ? null : Number(values.min),
    med: values.med === undefined ? null : Number(values.med),
    p90: values["p(90)"] === undefined ? null : Number(values["p(90)"]),
    p95: values["p(95)"] === undefined ? null : Number(values["p(95)"]),
    p99: values["p(99)"] === undefined ? null : Number(values["p(99)"]),
    max: values.max === undefined ? null : Number(values.max),
  };
}

export function handleSummary(data) {
  let startOffsetSeconds = 0;
  const phases = PHASES.map((phase) => {
    const scenarioName = `arrival_${metricSuffix(phase.phase_id)}`;
    const accepted = metricCount(data, `${scenarioName}_accepted_202`) || 0;
    const failed = metricCount(data, `${scenarioName}_failed`) || 0;
    const result = {
      phase_id: phase.phase_id,
      profile: phase.profile,
      scenario_name: scenarioName,
      target_rate: phase.target_rate,
      time_unit: "1s",
      duration_seconds: phase.duration_seconds,
      start_offset_seconds: startOffsetSeconds,
      end_offset_seconds: startOffsetSeconds + phase.duration_seconds,
      pre_allocated_vus: PRE_ALLOCATED_VUS,
      max_vus: MAX_VUS,
      accepted_202: accepted,
      failed,
      iterations: metricCount(data, `iterations{scenario:${scenarioName}}`),
      dropped_iterations: metricCount(
        data,
        `dropped_iterations{scenario:${scenarioName}}`,
      ),
      http_req_failed_rate: metricRate(
        data,
        `http_req_failed{scenario:${scenarioName}}`,
      ),
      iteration_duration_ms: metricTrend(
        data,
        `iteration_duration{scenario:${scenarioName}}`,
      ),
      http_req_duration_ms: metricTrend(
        data,
        `http_req_duration{scenario:${scenarioName}}`,
      ),
      http_accepted_rate_per_second:
        phase.duration_seconds > 0 ? accepted / phase.duration_seconds : null,
    };
    startOffsetSeconds += phase.duration_seconds;
    return result;
  });
  const summary = {
    schema_version: "ops.recovery-arrival-workload.v1",
    executor: "constant-arrival-rate",
    time_unit: "1s",
    stream_count: STREAM_COUNT,
    pre_allocated_vus: PRE_ALLOCATED_VUS,
    max_vus: MAX_VUS,
    phases,
    dropped_iterations: metricCount(data, "dropped_iterations") || 0,
    http_requests: metricCount(data, "http_reqs") || 0,
    checks_rate: Number(data.metrics?.checks?.values?.rate || 0),
    vus: metricGauge(data, "vus"),
    vus_max: metricGauge(data, "vus_max"),
    iteration_duration_ms: metricTrend(data, "iteration_duration"),
    http_req_duration_ms: metricTrend(data, "http_req_duration"),
  };
  return { stdout: `PHASE4_K6_SUMMARY=${JSON.stringify(summary)}\n` };
}
