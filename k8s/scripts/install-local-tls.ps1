param(
  [string]$Namespace = "messaging-app",
  [string]$SecretName = "messaging-local-tls",
  [string[]]$DnsNames = @("localhost"),
  [int]$ValidDays = 365
)

$ErrorActionPreference = "Stop"

function Convert-BytesToPem([byte[]]$Bytes, [string]$Label) {
  $base64 = [Convert]::ToBase64String($Bytes)
  $chunks = [System.Collections.Generic.List[string]]::new()
  for ($i = 0; $i -lt $base64.Length; $i += 64) {
    $length = [Math]::Min(64, $base64.Length - $i)
    $chunks.Add($base64.Substring($i, $length))
  }

  return @(
    "-----BEGIN $Label-----"
    $chunks
    "-----END $Label-----"
  ) -join [Environment]::NewLine
}

function Get-PrivateKeyBytes($Rsa) {
  $method = $Rsa.GetType().GetMethod("ExportPkcs8PrivateKey", [Type[]]@())
  if ($method) {
    return $Rsa.ExportPkcs8PrivateKey()
  }

  if ($Rsa -is [System.Security.Cryptography.RSACng]) {
    return $Rsa.Key.Export([System.Security.Cryptography.CngKeyBlobFormat]::Pkcs8PrivateBlob)
  }

  throw "Unable to export private key in PKCS#8 format on this PowerShell/.NET runtime."
}

$rsa = [System.Security.Cryptography.RSA]::Create(2048)
$subject = "CN=$($DnsNames[0])"
$request = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
  $subject,
  $rsa,
  [System.Security.Cryptography.HashAlgorithmName]::SHA256,
  [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
)

$sanBuilder = [System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
foreach ($dnsName in $DnsNames) {
  $sanBuilder.AddDnsName($dnsName)
}

$request.CertificateExtensions.Add($sanBuilder.Build())
$request.CertificateExtensions.Add(
  [System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $false)
)
$request.CertificateExtensions.Add(
  [System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
    [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature -bor
    [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyEncipherment,
    $false
  )
)
$eku = [System.Security.Cryptography.OidCollection]::new()
$null = $eku.Add([System.Security.Cryptography.Oid]::new("1.3.6.1.5.5.7.3.1", "Server Authentication"))
$request.CertificateExtensions.Add(
  [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($eku, $false)
)

$notBefore = [DateTimeOffset]::UtcNow.AddMinutes(-5)
$notAfter = $notBefore.AddDays($ValidDays)
$certificate = $request.CreateSelfSigned($notBefore, $notAfter)

$certPem = Convert-BytesToPem -Bytes $certificate.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert) -Label "CERTIFICATE"
$keyPem = Convert-BytesToPem -Bytes (Get-PrivateKeyBytes -Rsa $rsa) -Label "PRIVATE KEY"

$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("messaging-local-tls-" + [guid]::NewGuid().ToString("N"))
[System.IO.Directory]::CreateDirectory($tempDir) | Out-Null
$certPath = Join-Path $tempDir "tls.crt"
$keyPath = Join-Path $tempDir "tls.key"

try {
  [System.IO.File]::WriteAllText($certPath, $certPem + [Environment]::NewLine, [System.Text.Encoding]::ASCII)
  [System.IO.File]::WriteAllText($keyPath, $keyPem + [Environment]::NewLine, [System.Text.Encoding]::ASCII)

  kubectl create secret tls $SecretName `
    -n $Namespace `
    --cert $certPath `
    --key $keyPath `
    --dry-run=client `
    -o yaml | kubectl apply -f - | Out-Host
}
finally {
  if (Test-Path $tempDir) {
    Remove-Item -LiteralPath $tempDir -Recurse -Force
  }
  $certificate.Dispose()
  $rsa.Dispose()
}
