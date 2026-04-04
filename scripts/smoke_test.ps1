$ErrorActionPreference = "Stop"

$health = Invoke-RestMethod -Method Get -Uri http://localhost/api/health/ready
if ($health.status -ne "ready") {
  throw "Service is not ready"
}

$suffix = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$u1Body = @{ username = "smoke_alice_$suffix" } | ConvertTo-Json
$u2Body = @{ username = "smoke_bob_$suffix" } | ConvertTo-Json

$u1 = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/users -ContentType "application/json" -Body $u1Body
$u2 = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/users -ContentType "application/json" -Body $u2Body

$roomBody = @{ name = "smoke-room-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json
$room = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/rooms -ContentType "application/json" -Body $roomBody

$msgBody = @{ user_id = $u1.id; body = "hello smoke" } | ConvertTo-Json
$accepted = Invoke-RestMethod -Method Post -Uri "http://localhost/api/v1/rooms/$($room.id)/messages" -Headers @{"X-Idempotency-Key"="smoke-msg-$suffix"} -ContentType "application/json" -Body $msgBody

$requestId = $accepted.request_id
$messageId = $null
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
  $status = Invoke-RestMethod -Method Get -Uri "http://localhost/api/v1/message-requests/$requestId"
  if ($status.status -eq "persisted" -and $status.message_id) {
    $messageId = $status.message_id
    break
  }
  Start-Sleep -Milliseconds 500
}
if ($null -eq $messageId) {
  throw "Message was not persisted in time"
}

$messages = Invoke-RestMethod -Method Get -Uri "http://localhost/api/v1/rooms/$($room.id)/messages"

$readBody = @{ user_id = $u2.id } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://localhost/api/v1/messages/$messageId/read" -ContentType "application/json" -Body $readBody | Out-Null

$unread = Invoke-RestMethod -Method Get -Uri "http://localhost/api/v1/rooms/$($room.id)/unread-count/$($u2.id)"
$observer = Invoke-RestMethod -Method Get -Uri http://localhost/observer/events

Write-Host "health=$($health.status) message_count=$($messages.Count) unread=$($unread.unread) observer_messages=$($observer.messages.Count)"
