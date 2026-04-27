import http from "k6/http";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost";
const STAGE_DURATION = __ENV.STAGE_DURATION || "60s";
const THINK_TIME = Number(__ENV.THINK_TIME || "0.2");
const PROFILE = __ENV.K6_PROFILE || "mixed";
const SINGLE_VUS = Number(__ENV.K6_SINGLE_VUS || "500");
const K6_WRITE_RESULT_FILES =
  (__ENV.K6_WRITE_RESULT_FILES || "true").toLowerCase() !== "false";
const SETUP_RETRIES = Number(__ENV.SETUP_RETRIES || "5");
const SETUP_RETRY_SLEEP = Number(__ENV.SETUP_RETRY_SLEEP || "1");
const SEND_IDEMPOTENCY_KEY =
  (__ENV.SEND_IDEMPOTENCY_KEY || "false").toLowerCase() === "true";

const eventStatus200 = new Counter("event_status_200");
const eventStatus401 = new Counter("event_status_401");
const eventStatus403 = new Counter("event_status_403");
const eventStatus404 = new Counter("event_status_404");
const eventStatus409 = new Counter("event_status_409");
const eventStatus500 = new Counter("event_status_500");
const eventStatus503 = new Counter("event_status_503");
const eventStatusOther = new Counter("event_status_other");

function pad2(value) {
  return String(value).padStart(2, "0");
}

function buildTimestamp() {
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = pad2(now.getMonth() + 1);
  const dd = pad2(now.getDate());
  const hh = pad2(now.getHours());
  const mi = pad2(now.getMinutes());
  const ss = pad2(now.getSeconds());
  return `${yyyy}${mm}${dd}-${hh}${mi}${ss}`;
}

function secondsFromDuration(duration) {
  const unit = duration.slice(-1);
  const value = Number(duration.slice(0, -1));
  if (unit === "s") return value;
  if (unit === "m") return value * 60;
  return value;
}

const stageSeconds = secondsFromDuration(STAGE_DURATION);

function buildScenarios() {
  if (PROFILE === "single500") {
    return {
      users_500: {
        executor: "constant-vus",
        vus: SINGLE_VUS,
        duration: STAGE_DURATION,
        exec: "eventFlow",
        startTime: "0s",
      },
    };
  }
  return {
    users_100: {
      executor: "constant-vus",
      vus: 100,
      duration: STAGE_DURATION,
        exec: "eventFlow",
      startTime: "0s",
    },
    users_500: {
      executor: "constant-vus",
      vus: 500,
      duration: STAGE_DURATION,
        exec: "eventFlow",
      startTime: `${stageSeconds}s`,
    },
    users_1000: {
      executor: "constant-vus",
      vus: 1000,
      duration: STAGE_DURATION,
        exec: "eventFlow",
      startTime: `${stageSeconds * 2}s`,
    },
  };
}

export const options = {
  scenarios: buildScenarios(),
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<1000", "p(99)<2000"],
  },
};

function metricValue(data, metricName, key, fallback = "n/a") {
  const metric = data.metrics?.[metricName];
  const values = metric?.values || {};
  return values[key] !== undefined ? values[key] : fallback;
}

function postJsonWithRetry(url, payload, params, expectedStatus, label) {
  let lastResponse;
  for (let attempt = 1; attempt <= SETUP_RETRIES; attempt += 1) {
    lastResponse = http.post(url, JSON.stringify(payload), params);
    if (lastResponse.status === expectedStatus) {
      return lastResponse;
    }
    if (attempt < SETUP_RETRIES) {
      sleep(SETUP_RETRY_SLEEP);
    }
  }

  check(lastResponse, {
    [`${label} (${expectedStatus})`]: (r) => r.status === expectedStatus,
  });
  return lastResponse;
}

export function setup() {
  const suffix = Date.now();
  const headers = { "Content-Type": "application/json" };
  const password = "Password123!";

  const u1Res = postJsonWithRetry(
    `${BASE_URL}/v1/users`,
    { username: `k6_user_a_${suffix}`, password },
    { headers },
    200,
    "create user a",
  );
  const u2Res = postJsonWithRetry(
    `${BASE_URL}/v1/users`,
    { username: `k6_user_b_${suffix}`, password },
    { headers },
    200,
    "create user b",
  );

  check(u1Res, { "create user a (200)": (r) => r.status === 200 });
  check(u2Res, { "create user b (200)": (r) => r.status === 200 });

  const u1 = JSON.parse(u1Res.body);
  const u2 = JSON.parse(u2Res.body);

  const loginHeaders = { "Content-Type": "application/json" };
  const u1LoginRes = postJsonWithRetry(
    `${BASE_URL}/v1/auth/login`,
    { username: `k6_user_a_${suffix}`, password },
    { headers: loginHeaders },
    200,
    "login user a",
  );
  check(u1LoginRes, { "login user a (200)": (r) => r.status === 200 });
  const u1Token = JSON.parse(u1LoginRes.body).access_token;

  const authHeaders = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${u1Token}`,
  };

  const streamRes = postJsonWithRetry(
    `${BASE_URL}/v1/streams`,
    { name: `k6-stream-${suffix}`, member_ids: [u1.id, u2.id] },
    { headers: authHeaders },
    200,
    "create stream",
  );
  check(streamRes, { "create stream (200)": (r) => r.status === 200 });
  const stream = JSON.parse(streamRes.body);

  return { streamId: stream.id, token: u1Token };
}

export function eventFlow(data) {
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${data.token}`,
  };
  if (SEND_IDEMPOTENCY_KEY) {
    headers["X-Idempotency-Key"] = `${__VU}-${__ITER}-${Date.now()}`;
  }

  const payload = JSON.stringify({
    body: `k6 event vu=${__VU} iter=${__ITER}`,
  });

  const res = http.post(
    `${BASE_URL}/v1/streams/${data.streamId}/events`,
    payload,
    { headers },
  );
  if (res.status === 200) eventStatus200.add(1);
  else if (res.status === 401) eventStatus401.add(1);
  else if (res.status === 403) eventStatus403.add(1);
  else if (res.status === 404) eventStatus404.add(1);
  else if (res.status === 409) eventStatus409.add(1);
  else if (res.status === 500) eventStatus500.add(1);
  else if (res.status === 503) eventStatus503.add(1);
  else eventStatusOther.add(1);

  check(res, {
    "event request accepted (200)": (r) => r.status === 200,
  });

  sleep(THINK_TIME);
}

export function handleSummary(data) {
  const timestamp = buildTimestamp();
  const baseName = `k6/results/${PROFILE}-${timestamp}`;
  const totalRequests = metricValue(data, "http_reqs", "count", 0);
  const failureRate = metricValue(data, "http_req_failed", "rate", 0);
  const avgLatency = metricValue(data, "http_req_duration", "avg", 0);
  const p95Latency = metricValue(data, "http_req_duration", "p(95)", 0);
  const p99Latency = metricValue(data, "http_req_duration", "p(99)", 0);
  const eventStatusLines = [
    ["200", "event_status_200"],
    ["401", "event_status_401"],
    ["403", "event_status_403"],
    ["404", "event_status_404"],
    ["409", "event_status_409"],
    ["500", "event_status_500"],
    ["503", "event_status_503"],
    ["other", "event_status_other"],
  ]
    .map(([label, metricName]) => {
      const count = data.metrics?.[metricName]?.values?.count || 0;
      return `Event status ${label.padEnd(5)}: ${count}`;
    });

  const summary = [
    "=== k6 Load Test Summary ===",
    `Base URL           : ${BASE_URL}`,
    `Profile            : ${PROFILE}`,
    `Stage duration     : ${STAGE_DURATION}${PROFILE === "single500" ? ` (${SINGLE_VUS} concurrent users)` : " (100 -> 500 -> 1000 concurrent users)"}`,
    `Idempotency header  : ${SEND_IDEMPOTENCY_KEY ? "enabled" : "disabled"}`,
    `Total HTTP requests: ${totalRequests}`,
    `Error rate         : ${(failureRate * 100).toFixed(2)}%`,
    `Latency avg (ms)   : ${Number(avgLatency).toFixed(2)}`,
    `Latency p95 (ms)   : ${Number(p95Latency).toFixed(2)}`,
    `Latency p99 (ms)   : ${Number(p99Latency).toFixed(2)}`,
    ...eventStatusLines,
    "============================",
  ].join("\n");

  return {
    ...(K6_WRITE_RESULT_FILES
      ? {
          [`${baseName}.txt`]: `${summary}\n`,
          [`${baseName}.json`]: JSON.stringify(data, null, 2),
        }
      : {}),
    stdout: `${summary}\n`,
  };
}
