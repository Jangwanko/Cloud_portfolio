import html
import os
from datetime import datetime

import psycopg2
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from psycopg2.extras import RealDictCursor

app = FastAPI(title="Message Observer", version="1.0.0")

DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "portfolio")
DB_USER = os.getenv("DB_USER", "portfolio")
DB_PASSWORD = os.getenv("DB_PASSWORD", "portfolio")


def get_conn():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


@app.get("/", response_class=HTMLResponse)
def observer_home() -> str:
    return """
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>Observer</title>
  <style>
    body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 20px; background: #0f172a; color: #e2e8f0; }
    h1 { margin-bottom: 8px; }
    .card { background: #111827; border: 1px solid #334155; border-radius: 10px; padding: 12px; margin-bottom: 12px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border-bottom: 1px solid #334155; text-align: left; padding: 6px; font-size: 13px; }
    .meta { color: #94a3b8; font-size: 12px; }
  </style>
</head>
<body>
  <h1>Message Send/Receive Observer</h1>
  <div class=\"meta\">Auto refreshes every 5 seconds.</div>
  <div class=\"card\">
    <h3>Message Timeline (send)</h3>
    <table id=\"sendTable\"><thead><tr><th>id</th><th>room</th><th>user</th><th>body</th><th>created_at</th></tr></thead><tbody></tbody></table>
  </div>
  <div class=\"card\">
    <h3>Notification Timeline (receive by worker)</h3>
    <table id=\"recvTable\"><thead><tr><th>id</th><th>message_id</th><th>room</th><th>payload</th><th>processed_at</th></tr></thead><tbody></tbody></table>
  </div>

<script>
async function loadData() {
  const res = await fetch('/events');
  const data = await res.json();

  const sendBody = document.querySelector('#sendTable tbody');
  sendBody.innerHTML = data.messages.map(m =>
    `<tr><td>${m.id}</td><td>${m.room_id}</td><td>${m.user_id}</td><td>${m.body}</td><td>${m.created_at}</td></tr>`
  ).join('');

  const recvBody = document.querySelector('#recvTable tbody');
  recvBody.innerHTML = data.attempts.map(a =>
    `<tr><td>${a.id}</td><td>${a.message_id}</td><td>${a.room_id}</td><td>${a.payload}</td><td>${a.processed_at}</td></tr>`
  ).join('');
}

loadData();
setInterval(loadData, 5000);
</script>
</body>
</html>
"""


@app.get("/events")
def events() -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, room_id, user_id, body, created_at
                FROM messages
                ORDER BY id DESC
                LIMIT 30
                """
            )
            messages = cur.fetchall()

            cur.execute(
                """
                SELECT id, message_id, room_id, payload, processed_at
                FROM notification_attempts
                ORDER BY id DESC
                LIMIT 30
                """
            )
            attempts = cur.fetchall()

    for row in messages:
        row["body"] = html.escape(str(row["body"]))
        if isinstance(row["created_at"], datetime):
            row["created_at"] = row["created_at"].isoformat()

    for row in attempts:
        row["payload"] = html.escape(str(row["payload"]))
        if isinstance(row["processed_at"], datetime):
            row["processed_at"] = row["processed_at"].isoformat()

    return {"messages": messages, "attempts": attempts}
