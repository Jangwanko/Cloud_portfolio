param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [int]$EventCount = 100,
  [int]$PersistTimeoutSec = 120,
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"

function Assert-Condition([bool]$Condition, [string]$Message) {
  if (-not $Condition) {
    throw $Message
  }
}

function New-EventBody([int]$Index) {
  return "ordering-event-{0:D4}" -f $Index
}

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" `
    -BaseUrl $BaseUrl `
    -Namespace $Namespace `
    -DbDeployment $DbDeployment
}

try {
  $suffix = "{0}-{1}" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), (Get-Random -Maximum 999999)
  $u1Name = "order_a_" + ([guid]::NewGuid().ToString("N").Substring(0, 10))
  $u2Name = "order_b_" + ([guid]::NewGuid().ToString("N").Substring(0, 10))
  $password = "Password123!"

  $u1 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)
  $u2 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u2Name; password = $password } | ConvertTo-Json)
  $token = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)).access_token
  $headers = @{ Authorization = "Bearer $token" }

  $stream = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/v1/streams" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body (@{ name = "ordering-stream-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)

  $accepted = [System.Collections.Generic.List[object]]::new()
  for ($i = 1; $i -le $EventCount; $i++) {
    $body = New-EventBody -Index $i
    $response = Invoke-RestMethod `
      -Method Post `
      -Uri "$BaseUrl/v1/streams/$($stream.id)/events" `
      -Headers $headers `
      -ContentType "application/json" `
      -Body (@{ body = $body } | ConvertTo-Json)

    Assert-Condition ($response.status -eq "accepted") "Unexpected accept status for index=$i status=$($response.status)"
    [void]$accepted.Add([pscustomobject]@{
      index = $i
      body = $body
      request_id = $response.request_id
    })
  }

  $deadline = (Get-Date).AddSeconds($PersistTimeoutSec)
  $events = @()
  while ((Get-Date) -lt $deadline) {
    $events = Invoke-RestMethod `
      -Method Get `
      -Uri "$BaseUrl/v1/streams/$($stream.id)/events?limit=$EventCount" `
      -Headers $headers

    $matchedCount = @($events | Where-Object { $_.body -like "ordering-event-*" }).Count
    if ($matchedCount -ge $EventCount) {
      break
    }
    Start-Sleep -Milliseconds 250
  }

  $ordered = @($events |
    Where-Object { $_.body -like "ordering-event-*" } |
    Sort-Object -Property stream_seq)

  Assert-Condition ($ordered.Count -eq $EventCount) "Expected $EventCount persisted events, got $($ordered.Count)"

  $seenSeq = @{}
  for ($i = 1; $i -le $EventCount; $i++) {
    $event = $ordered[$i - 1]
    $expectedBody = New-EventBody -Index $i
    Assert-Condition ([int]$event.stream_seq -eq $i) "Expected stream_seq=$i got=$($event.stream_seq) body=$($event.body)"
    Assert-Condition ($event.body -eq $expectedBody) "Expected body=$expectedBody at stream_seq=$i got=$($event.body)"
    Assert-Condition (-not $seenSeq.ContainsKey([string]$event.stream_seq)) "Duplicate stream_seq=$($event.stream_seq)"
    $seenSeq[[string]$event.stream_seq] = $true
  }

  $result = [ordered]@{
    stream_id = $stream.id
    event_count = $EventCount
    first_stream_seq = [int]$ordered[0].stream_seq
    last_stream_seq = [int]$ordered[-1].stream_seq
    first_body = $ordered[0].body
    last_body = $ordered[-1].body
    ordering = "pass"
  }

  $result | ConvertTo-Json -Depth 4
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" `
      -BaseUrl $BaseUrl `
      -Namespace $Namespace `
      -DbDeployment $DbDeployment
  }
}
