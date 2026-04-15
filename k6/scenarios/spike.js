import { setup, chatFlow, handleSummary } from "../../scripts/load_test_k6.js";

export { setup, chatFlow, handleSummary };

export const options = {
  scenarios: {
    warmup: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "15s", target: 50 },
        { duration: "15s", target: 300 },
        { duration: "10s", target: 50 },
      ],
      exec: "chatFlow",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<2000"],
  },
};
