ORDER_EVENT_CATEGORY_MAP = {
    "payment_completed": "payment",
    "payment_failed": "payment",
    "order_created": "order",
    "order_cancelled": "order",
    "delivery_started": "delivery",
    "delivery_delayed": "delivery",
    "refund_requested": "refund",
    "refund_completed": "refund",
    "support_requested": "support",
}


def classify_order_event(event_type: str) -> str:
    return ORDER_EVENT_CATEGORY_MAP.get(event_type, "needs_review")
