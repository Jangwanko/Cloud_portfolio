import { setup, chatFlow, handleSummary } from "../../scripts/load_test_k6.js";

export { setup, chatFlow, handleSummary };

export const options = {
  scenarios: {
    soak: {
      executor: "constant-vus",
      vus: 100,
      duration: "5m",
      exec: "chatFlow",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.02"],
    http_req_duration: ["p(95)<1500"],
  },
};
