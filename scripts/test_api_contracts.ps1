param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"

function Assert-Equal($Actual, $Expected, [string]$Message) {
  if ($Actual -ne $Expected) {
    throw "$Message expected=$Expected actual=$Actual"
  }
}

function Assert-True([bool]$Condition, [string]$Message) {
  if (-not $Condition) {
    throw $Message
  }
}

function Assert-HasProperty($Object, [string]$Name, [string]$Context) {
  if ($null -eq $Object -or -not ($Object.PSObject.Properties.Name -contains $Name)) {
    throw "$Context is missing property '$Name'"
  }
}

function Assert-HttpStatus([string]$Method, [string]$Uri, [int]$ExpectedStatus, $Headers = @{}, $Body = $null) {
  try {
    $params = @{
      Method = $Method
      Uri = $Uri
      Headers = $Headers
      TimeoutSec = 10
    }
    if ($null -ne $Body) {
      $params.ContentType = "application/json"
      $params.Body = $Body
    }
    $response = Invoke-WebRequest @params
    $status = [int]$response.StatusCode
  } catch {
    if ($null -eq $_.Exception.Response) {
      throw
    }
    $status = [int]$_.Exception.Response.StatusCode
  }

  if ($status -ne $ExpectedStatus) {
    throw "Expected HTTP $ExpectedStatus from $Method $Uri, got $status"
  }
}

function Wait-Ready() {
  $deadline = (Get-Date).AddSeconds(180)
  while ((Get-Date) -lt $deadline) {
    try {
      $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health/ready" -TimeoutSec 5
      if ($health.status -eq "ready") { return $health }
    } catch {
      Start-Sleep -Seconds 2
      continue
    }
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for readiness"
}

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
}

try {
  $health = Wait-Ready
  Assert-Equal $health.status "ready" "readiness status"
  Assert-Equal $health.queue_backend "kafka" "readiness queue backend"
  Assert-HasProperty $health "kafka" "readiness"
  Assert-HasProperty $health "postgres" "readiness"
  Assert-HasProperty $health.kafka "bootstrap_reachable" "readiness.kafka"
  Assert-Equal $health.kafka.bootstrap_reachable $true "readiness.kafka.bootstrap_reachable"
  Assert-HasProperty $health.postgres "primary_reachable" "readiness.postgres"

  $suffix = "{0}-{1}" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), (Get-Random -Maximum 999999)
  $password = "Password123!"
  $u1Name = "contract" + ([guid]::NewGuid().ToString("N").Substring(0, 10))
  $u2Name = "contract" + ([guid]::NewGuid().ToString("N").Substring(0, 10))
  $outsiderName = "contract" + ([guid]::NewGuid().ToString("N").Substring(0, 10))

  $u1 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)
  $u2 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u2Name; password = $password } | ConvertTo-Json)
  $outsider = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $outsiderName; password = $password } | ConvertTo-Json)

  foreach ($user in @($u1, $u2, $outsider)) {
    Assert-HasProperty $user "id" "user response"
    Assert-HasProperty $user "username" "user response"
    Assert-True ([int]$user.id -gt 0) "user id must be positive"
  }

  $u1Login = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)
  $u2Login = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $u2Name; password = $password } | ConvertTo-Json)
  $outsiderLogin = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $outsiderName; password = $password } | ConvertTo-Json)

  Assert-Equal $u1Login.token_type "bearer" "login token_type"
  Assert-HasProperty $u1Login "access_token" "login response"
  Assert-HasProperty $u1Login "user" "login response"
  Assert-Equal ([int]$u1Login.user.id) ([int]$u1.id) "login user id"

  $u1Headers = @{ Authorization = "Bearer $($u1Login.access_token)" }
  $u2Headers = @{ Authorization = "Bearer $($u2Login.access_token)" }
  $outsiderHeaders = @{ Authorization = "Bearer $($outsiderLogin.access_token)" }

  Assert-HttpStatus -Method "GET" -Uri "$BaseUrl/v1/dlq/ingress?limit=1" -ExpectedStatus 401
  Assert-HttpStatus -Method "POST" -Uri "$BaseUrl/v1/auth/login" -ExpectedStatus 401 -Body (@{ username = $u1Name; password = "wrong-password" } | ConvertTo-Json)

  $stream = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams" -Headers $u1Headers -ContentType "application/json" -Body (@{ name = "contract-stream-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)
  Assert-HasProperty $stream "id" "stream response"
  Assert-HasProperty $stream "name" "stream response"
  Assert-HasProperty $stream "member_ids" "stream response"
  Assert-True (@($stream.member_ids) -contains [int]$u1.id) "stream must include owner"
  Assert-True (@($stream.member_ids) -contains [int]$u2.id) "stream must include requested member"

  Assert-HttpStatus -Method "GET" -Uri "$BaseUrl/v1/streams/$($stream.id)/events" -ExpectedStatus 401
  Assert-HttpStatus -Method "GET" -Uri "$BaseUrl/v1/streams/$($stream.id)/events" -ExpectedStatus 403 -Headers $outsiderHeaders
  Assert-HttpStatus -Method "POST" -Uri "$BaseUrl/v1/streams/999999999/events" -ExpectedStatus 404 -Headers $u1Headers -Body (@{ body = "missing stream" } | ConvertTo-Json)

  $eventBody = "contract event $suffix"
  $eventHeaders = @{ Authorization = "Bearer $($u1Login.access_token)"; "X-Idempotency-Key" = "contract-event-$suffix" }
  $accepted = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams/$($stream.id)/events" -Headers $eventHeaders -ContentType "application/json" -Body (@{ body = $eventBody } | ConvertTo-Json)
  Assert-Equal $accepted.status "accepted" "accepted event status"
  Assert-Equal $accepted.persistence "queued" "accepted event persistence"
  Assert-Equal ([int]$accepted.stream_id) ([int]$stream.id) "accepted stream_id"
  Assert-Equal ([int]$accepted.user_id) ([int]$u1.id) "accepted user_id"
  Assert-Equal $accepted.body $eventBody "accepted body"
  Assert-HasProperty $accepted "request_id" "accepted event response"
  Assert-HasProperty $accepted "queued_at" "accepted event response"

  $requestId = $accepted.request_id
  $persisted = $null
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Date) -lt $deadline) {
    try {
      $status = Invoke-RestMethod -Method Get -Headers $u1Headers -Uri "$BaseUrl/v1/event-requests/$requestId" -TimeoutSec 5
      if ($status.status -eq "persisted" -and $status.event_id) {
        $persisted = $status
        break
      }
    } catch {
      Start-Sleep -Milliseconds 500
      continue
    }
    Start-Sleep -Milliseconds 500
  }
  if ($null -eq $persisted) {
    throw "Event request did not become persisted in time"
  }

  Assert-Equal $persisted.status "persisted" "request status"
  Assert-Equal ([int]$persisted.stream_id) ([int]$stream.id) "request status stream_id"
  Assert-Equal ([int]$persisted.stream_seq) 1 "request status stream_seq"
  Assert-Equal ([int]$persisted.user_id) ([int]$u1.id) "request status user_id"
  Assert-HasProperty $persisted "event_id" "request status"
  Assert-HasProperty $persisted "created_at" "request status"

  Assert-HttpStatus -Method "GET" -Uri "$BaseUrl/v1/event-requests/$requestId" -ExpectedStatus 403 -Headers $u2Headers

  $eventsResponse = Invoke-RestMethod -Method Get -Headers $u1Headers -Uri "$BaseUrl/v1/streams/$($stream.id)/events?limit=10"
  if ($eventsResponse.PSObject.Properties.Name -contains "value") {
    $events = @($eventsResponse.value)
  } else {
    $events = @($eventsResponse)
  }
  Assert-True ($events.Count -ge 1) "events list must contain persisted event"
  $event = $events | Where-Object { $_.request_id -eq $requestId } | Select-Object -First 1
  Assert-True ($null -ne $event) "events list must include request_id=$requestId"
  Assert-Equal ([int]$event.id) ([int]$persisted.event_id) "event id"
  Assert-Equal ([int]$event.stream_seq) 1 "event stream_seq"
  Assert-Equal $event.body $eventBody "event body"
  Assert-HasProperty $event "created_at" "event list item"

  $unreadBefore = Invoke-RestMethod -Method Get -Headers $u2Headers -Uri "$BaseUrl/v1/streams/$($stream.id)/unread-count/$($u2.id)"
  Assert-Equal ([int]$unreadBefore.unread) 1 "unread before read"

  $read = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/events/$($persisted.event_id)/read" -Headers $u2Headers -ContentType "application/json" -Body "{}"
  Assert-Equal $read.status "ok" "read receipt status"
  Assert-Equal ([int]$read.event_id) ([int]$persisted.event_id) "read receipt event_id"
  Assert-Equal ([int]$read.user_id) ([int]$u2.id) "read receipt user_id"

  $unreadAfter = Invoke-RestMethod -Method Get -Headers $u2Headers -Uri "$BaseUrl/v1/streams/$($stream.id)/unread-count/$($u2.id)"
  Assert-Equal ([int]$unreadAfter.unread) 0 "unread after read"
  Assert-HttpStatus -Method "GET" -Uri "$BaseUrl/v1/streams/$($stream.id)/unread-count/$($u2.id)" -ExpectedStatus 403 -Headers $u1Headers

  $dlq = Invoke-RestMethod -Method Get -Headers $u1Headers -Uri "$BaseUrl/v1/dlq/ingress?limit=5"
  Assert-Equal $dlq.queue_backend "kafka" "dlq queue backend"
  Assert-HasProperty $dlq "topic" "dlq response"
  Assert-HasProperty $dlq "count" "dlq response"
  Assert-HasProperty $dlq "max_replay_count" "dlq response"
  Assert-HasProperty $dlq "items" "dlq response"

  Write-Host "API contract test passed: stream_id=$($stream.id) request_id=$requestId event_id=$($persisted.event_id)"
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
  }
}
