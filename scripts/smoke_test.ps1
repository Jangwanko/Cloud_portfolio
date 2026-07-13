param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
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

$streamBody = @{ name = "smoke-stream-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json
$stream = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams" -Headers @{ Authorization = "Bearer $u1Token" } -ContentType "application/json" -Body $streamBody

$eventBody = @{
  event_type = "portfolio.smoke.probe"
  payload = @{ message = "hello smoke" }
  metadata = @{ scenario = "smoke-test" }
} | ConvertTo-Json -Depth 4
$acceptedResponse = Invoke-WebRequest -Method Post -Uri "$BaseUrl/v2/streams/$($stream.id)/events" -Headers @{ Authorization = "Bearer $u1Token"; "X-Idempotency-Key"="smoke-event-$suffix"} -ContentType "application/json" -Body $eventBody
if ([int]$acceptedResponse.StatusCode -ne 202) {
  throw "Expected HTTP 202 from event intake, got $($acceptedResponse.StatusCode)"
}
$accepted = $acceptedResponse.Content | ConvertFrom-Json

if ($accepted.schema_version -ne 2 -or $accepted.event_type -ne "portfolio.smoke.probe") {
  throw "Generic event envelope was not accepted as expected"
}

$requestId = $accepted.request_id
$eventId = $null
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
  try {
    $status = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u1Token" } -Uri "$BaseUrl/v2/event-requests/$requestId"
    if ($status.status -eq "persisted" -and $status.event_id) {
      $eventId = $status.event_id
      break
    }
  } catch {
    Start-Sleep -Milliseconds 500
    continue
  }
  Start-Sleep -Milliseconds 500
}
if ($null -eq $eventId) {
  throw "Event was not persisted in time"
}

$events = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u1Token" } -Uri "$BaseUrl/v2/streams/$($stream.id)/events"
$eventItems = @($events.items)
if (@($eventItems | Where-Object { $_.request_id -eq $requestId -and $_.payload.message -eq "hello smoke" }).Count -ne 1) {
  throw "Persisted generic event payload was not returned by the read model"
}

Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/events/$eventId/read" -Headers @{ Authorization = "Bearer $u2Token" } -ContentType "application/json" -Body "{}" | Out-Null

$unread = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u2Token" } -Uri "$BaseUrl/v1/streams/$($stream.id)/unread-count/$($u2.id)"

Write-Host "health=$($health.status) event_count=$($eventItems.Count) event_source=$($events.source) unread=$($unread.unread)"
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
  }
}
