# Order Event Service Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the project look like a real post-order event service by adding order-domain API contracts and an operational category classifier on top of the existing Kafka intake path.

**Architecture:** Keep the current Kafka/Worker/message table internals intact. Add order-facing schemas and a `/v1/orders/{order_id}/events` endpoint that translates order events into the existing ingress job payload, then expose category metadata for operators.

**Tech Stack:** FastAPI, Pydantic, pytest, existing Kafka client helpers.

---

### Task 1: Order Event Classification Helper

**Files:**
- Create: `portfolio/order_events.py`
- Test: `tests/test_api_basic.py`

- [x] **Step 1: Write failing tests**

Add tests that import `classify_order_event` and assert:

```python
assert classify_order_event("payment_completed") == "payment"
assert classify_order_event("delivery_started") == "delivery"
assert classify_order_event("unknown_event") == "needs_review"
```

- [x] **Step 2: Run tests and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests\test_api_basic.py::TestOrderEventClassification -q`

Expected: import error because `portfolio.order_events` does not exist yet.

- [x] **Step 3: Implement minimal helper**

Create `portfolio/order_events.py` with a mapping for:

```python
payment_completed -> payment
payment_failed -> payment
order_created -> order
order_cancelled -> order
delivery_started -> delivery
delivery_delayed -> delivery
refund_requested -> refund
refund_completed -> refund
support_requested -> support
default -> needs_review
```

- [x] **Step 4: Run tests and verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests\test_api_basic.py::TestOrderEventClassification -q`

Expected: all classification tests pass.

### Task 2: Order Event API Contract

**Files:**
- Modify: `portfolio/schemas.py`
- Modify: `portfolio/api.py`
- Test: `tests/test_api_basic.py`

- [x] **Step 1: Write failing contract tests**

Add tests asserting OpenAPI includes:

```python
"/v1/orders/{order_id}/events"
"OrderEventCreate"
"OrderEventAcceptedResponse"
```

- [x] **Step 2: Run tests and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests\test_api_basic.py::TestOrderEventApiContract -q`

Expected: route and schemas are absent.

- [x] **Step 3: Add schemas**

Add `OrderEventCreate` and `OrderEventAcceptedResponse` to `portfolio/schemas.py`.

- [x] **Step 4: Add endpoint**

Add `POST /v1/orders/{order_id}/events` to `portfolio/api.py`. It should:
- accept `event_type`, `body`, `payment_id`
- derive `category` with `classify_order_event`
- append to Kafka using the existing `_store_request_and_queue_job`
- return `request_id`, `status`, `persistence`, `order_id`, `stream_id`, `event_type`, `category`, `queued_at`

- [x] **Step 5: Run tests and verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests\test_api_basic.py::TestOrderEventApiContract -q`

Expected: route and schema tests pass.

### Task 3: Regression Suite

**Files:**
- Existing test suite

- [x] **Step 1: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_api_basic.py -q`

Expected: all fast API helper tests pass.

- [x] **Step 2: Run full tests**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: full suite passes or report exact failures.

### Self-Review

- Covers the first service-facing slice: order event classification and order-domain API surface.
- Leaves database schema redesign, AI classification, automatic CS response, and UI work out of this first slice.
- Keeps existing internal `room_id` / `stream_id` implementation while exposing `order_id` at the service boundary.
