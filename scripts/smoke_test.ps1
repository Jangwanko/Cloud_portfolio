param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [string]$RedisDeployment = "messaging-redis-node",
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -RedisDeployment $RedisDeployment
}

try {
  $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health/ready"
  if ($health.status -ne "ready") {
    throw "Service is not ready"
  }

$suffix = "{0}-{1}" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), (Get-Random -Maximum 999999)
$u1Name = "u" + ([guid]::NewGuid().ToString("N").Substring(0, 12))
$u2Name = "u" + ([guid]::NewGuid().ToString("N").Substring(0, 12))
$u1Password = "Password123!"
$u2Password = "Password123!"
$u1Body = @{ username = $u1Name; password = $u1Password } | ConvertTo-Json
$u2Body = @{ username = $u2Name; password = $u2Password } | ConvertTo-Json

$u1 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body $u1Body
$u2 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body $u2Body
$u1Token = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $u1Name; password = $u1Password } | ConvertTo-Json)).access_token
$u2Token = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $u2Name; password = $u2Password } | ConvertTo-Json)).access_token

$roomBody = @{ name = "smoke-room-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json
$room = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/rooms" -Headers @{ Authorization = "Bearer $u1Token" } -ContentType "application/json" -Body $roomBody

$msgBody = @{ body = "hello smoke" } | ConvertTo-Json
$accepted = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/rooms/$($room.id)/messages" -Headers @{ Authorization = "Bearer $u1Token"; "X-Idempotency-Key"="smoke-msg-$suffix"} -ContentType "application/json" -Body $msgBody

$requestId = $accepted.request_id
$messageId = $null
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
  $status = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u1Token" } -Uri "$BaseUrl/v1/message-requests/$requestId"
  if ($status.status -eq "persisted" -and $status.message_id) {
    $messageId = $status.message_id
    break
  }
  Start-Sleep -Milliseconds 500
}
if ($null -eq $messageId) {
  throw "Message was not persisted in time"
}

$messages = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u1Token" } -Uri "$BaseUrl/v1/rooms/$($room.id)/messages"

Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/messages/$messageId/read" -Headers @{ Authorization = "Bearer $u2Token" } -ContentType "application/json" -Body "{}" | Out-Null

$unread = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u2Token" } -Uri "$BaseUrl/v1/rooms/$($room.id)/unread-count/$($u2.id)"

Write-Host "health=$($health.status) message_count=$($messages.Count) unread=$($unread.unread)"
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -RedisDeployment $RedisDeployment
  }
}
