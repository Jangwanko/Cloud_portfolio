import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost/api";
const STAGE_DURATION = __ENV.STAGE_DURATION || "60s";
const THINK_TIME = Number(__ENV.THINK_TIME || "0.2");
const PROFILE = __ENV.K6_PROFILE || "mixed";

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
        vus: 500,
        duration: STAGE_DURATION,
        exec: "chatFlow",
        startTime: "0s",
      },
    };
  }
  return {
    users_100: {
      executor: "constant-vus",
      vus: 100,
      duration: STAGE_DURATION,
      exec: "chatFlow",
      startTime: "0s",
    },
    users_500: {
      executor: "constant-vus",
      vus: 500,
      duration: STAGE_DURATION,
      exec: "chatFlow",
      startTime: `${stageSeconds}s`,
    },
    users_1000: {
      executor: "constant-vus",
      vus: 1000,
      duration: STAGE_DURATION,
      exec: "chatFlow",
      startTime: `${stageSeconds * 2}s`,
    },
  };
}

export const options = {
  scenarios: buildScenarios(),
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

export function setup() {
  const suffix = Date.now();
  const headers = { "Content-Type": "application/json" };

  const u1Res = http.post(
    `${BASE_URL}/v1/users`,
    JSON.stringify({ username: `k6_user_a_${suffix}` }),
    { headers },
  );
  const u2Res = http.post(
    `${BASE_URL}/v1/users`,
    JSON.stringify({ username: `k6_user_b_${suffix}` }),
    { headers },
  );

  check(u1Res, { "create user a (200)": (r) => r.status === 200 });
  check(u2Res, { "create user b (200)": (r) => r.status === 200 });

  const u1 = JSON.parse(u1Res.body);
  const u2 = JSON.parse(u2Res.body);

  const roomRes = http.post(
    `${BASE_URL}/v1/rooms`,
    JSON.stringify({ name: `k6-room-${suffix}`, member_ids: [u1.id, u2.id] }),
    { headers },
  );
  check(roomRes, { "create room (200)": (r) => r.status === 200 });
  const room = JSON.parse(roomRes.body);

  return { roomId: room.id, userId: u1.id };
}

export function chatFlow(data) {
  const headers = {
    "Content-Type": "application/json",
    "X-Idempotency-Key": `${__VU}-${__ITER}-${Date.now()}`,
  };

  const payload = JSON.stringify({
    user_id: data.userId,
    body: `k6 chat vu=${__VU} iter=${__ITER}`,
  });

  const res = http.post(
    `${BASE_URL}/v1/rooms/${data.roomId}/messages`,
    payload,
    { headers },
  );

  check(res, {
    "chat request accepted (200)": (r) => r.status === 200,
  });

  sleep(THINK_TIME);
}

export function handleSummary(data) {
  const totalRequests = metricValue(data, "http_reqs", "count", 0);
  const failureRate = metricValue(data, "http_req_failed", "rate", 0);
  const avgLatency = metricValue(data, "http_req_duration", "avg", 0);
  const p95Latency = metricValue(data, "http_req_duration", "p(95)", 0);
  const p99Latency = metricValue(data, "http_req_duration", "p(99)", 0);

  const summary = [
    "=== k6 Load Test Summary ===",
    `Base URL           : ${BASE_URL}`,
    `Profile            : ${PROFILE}`,
    `Stage duration     : ${STAGE_DURATION}${PROFILE === "single500" ? " (500 concurrent users)" : " (100 -> 500 -> 1000 concurrent users)"}`,
    `Total HTTP requests: ${totalRequests}`,
    `Error rate         : ${(failureRate * 100).toFixed(2)}%`,
    `Latency avg (ms)   : ${Number(avgLatency).toFixed(2)}`,
    `Latency p95 (ms)   : ${Number(p95Latency).toFixed(2)}`,
    `Latency p99 (ms)   : ${Number(p99Latency).toFixed(2)}`,
    "============================",
  ].join("\n");

  return {
    stdout: `${summary}\n`,
    "k6-summary.txt": `${summary}\n`,
    "k6-summary.json": JSON.stringify(data, null, 2),
  };
}
