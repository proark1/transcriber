[CmdletBinding()]
param(
    [string]$BaseUrl = $env:TRANSCRIBER_SMOKE_URL,
    [string]$Username = $env:TRANSCRIBER_SMOKE_USERNAME,
    [string]$Pin = $env:TRANSCRIBER_SMOKE_PIN,
    [string]$AudioPath,
    [ValidateSet("en", "de", "tr")]
    [string]$Language = "en",
    [int]$TimeoutSeconds = 7200
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

if (-not $BaseUrl -or -not $Username -or -not $Pin) {
    throw "Set TRANSCRIBER_SMOKE_URL, TRANSCRIBER_SMOKE_USERNAME, and TRANSCRIBER_SMOKE_PIN."
}
$BaseUrl = $BaseUrl.TrimEnd("/")
$Username = $Username.Trim().ToLowerInvariant()
if ($Username -notmatch '^railway-smoke-[a-z0-9]{8,16}$') {
    throw "Use a generated railway-smoke account so it can be removed safely."
}
if ($Pin -notmatch '^\d{6,12}$') {
    throw "The smoke PIN must contain 6-12 digits."
}
$baseUri = [Uri]$BaseUrl
if ($baseUri.Scheme -ne "https" -and $baseUri.Host -notin @("localhost", "127.0.0.1")) {
    throw "The smoke URL must use HTTPS."
}
$origin = "$($baseUri.Scheme)://$($baseUri.Authority)"
$webSession = $null
$csrfHeaders = $null
$httpClient = New-Object System.Net.Http.HttpClient
$upload = $null
$queued = $null
$recordingDeleted = $false

function Assert-LoginRejected {
    param(
        [Parameter(Mandatory)] [string]$RejectedUsername,
        [Parameter(Mandatory)] [string]$RejectedPin,
        [Parameter(Mandatory)] [int]$ExpectedStatus,
        [Parameter(Mandatory)] [string]$ExpectedCode,
        [Parameter(Mandatory)] [string]$ExpectedMessage
    )
    try {
        Invoke-RestMethod `
            -Uri "$BaseUrl/api/auth/login" `
            -Method Post `
            -Body (@{ username = $RejectedUsername; pin = $RejectedPin } | ConvertTo-Json -Compress) `
            -ContentType "application/json" | Out-Null
    } catch {
        $status = [int]$_.Exception.Response.StatusCode
        $problem = $_.ErrorDetails.Message | ConvertFrom-Json
        if (
            $status -ne $ExpectedStatus -or
            $problem.error.code -cne $ExpectedCode -or
            $problem.error.message -cne $ExpectedMessage
        ) {
            throw "A login rejection did not match the expected safe response."
        }
        return
    }
    throw "A login that should have been rejected unexpectedly succeeded."
}

function Invoke-AppJson {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [ValidateSet("Get", "Post", "Delete")] [string]$Method = "Get",
        [object]$Body,
        [hashtable]$Headers
    )
    $parameters = @{
        Uri = "$BaseUrl$Path"
        Method = $Method
        WebSession = $webSession
    }
    if ($null -ne $Headers) { $parameters.Headers = $Headers }
    if ($null -ne $Body) {
        $parameters.Body = $Body | ConvertTo-Json -Compress
        $parameters.ContentType = "application/json"
    }
    return Invoke-RestMethod @parameters
}

try {
    $ready = Invoke-RestMethod -Uri "$BaseUrl/readyz" -Method Get -SessionVariable webSession
    if ($ready.status -ne "ready") { throw "Readiness check did not pass." }

    Assert-LoginRejected `
        -RejectedUsername "OwNeR" `
        -RejectedPin $Pin `
        -ExpectedStatus 422 `
        -ExpectedCode "username_unavailable" `
        -ExpectedMessage "That username is unavailable."

    $login = Invoke-AppJson -Path "/api/auth/login" -Method Post -Body @{
        username = $Username.ToUpperInvariant()
        pin = $Pin
    }
    if (-not $login.accountCreated -or $login.username -cne $Username) {
        throw "The smoke account was not created with its normalized lowercase username."
    }
    $csrfHeaders = @{
        Origin = $origin
        "X-CSRF-Token" = $login.csrfToken
    }

    $wrongPin = if ($Pin -ceq "000000") { "111111" } else { "000000" }
    Assert-LoginRejected `
        -RejectedUsername $Username.ToUpperInvariant() `
        -RejectedPin $wrongPin `
        -ExpectedStatus 401 `
        -ExpectedCode "incorrect_pin" `
        -ExpectedMessage "That PIN is incorrect."

    $secondSession = $null
    $secondLogin = Invoke-RestMethod `
        -Uri "$BaseUrl/api/auth/login" `
        -Method Post `
        -SessionVariable secondSession `
        -Body (@{ username = $Username.ToUpperInvariant(); pin = $Pin } | ConvertTo-Json -Compress) `
        -ContentType "application/json"
    if ($secondLogin.accountCreated -or $secondLogin.username -cne $Username) {
        throw "Case-insensitive login did not reopen the existing account."
    }
    Invoke-RestMethod `
        -Uri "$BaseUrl/api/auth/logout" `
        -Method Post `
        -WebSession $secondSession `
        -Headers @{ Origin = $origin; "X-CSRF-Token" = $secondLogin.csrfToken } | Out-Null

    $recordingResponse = Invoke-AppJson -Path "/api/recordings"
    if ($null -eq $recordingResponse) {
        $recordings = @()
    } else {
        $recordings = @($recordingResponse)
    }
    Write-Output "Authenticated Railway web service; history contains $($recordings.Count) recording(s)."

    if (-not $AudioPath) {
        Write-Output "Authentication-only smoke check passed. Provide -AudioPath for a full transcription smoke check."
        return
    }

    $audio = Get-Item -LiteralPath $AudioPath
    if ($audio.Length -le 0 -or $audio.Length -gt 5000000000) {
        throw "The smoke audio must be non-empty and no larger than 5 GB."
    }
    $contentTypes = @{
        ".m4a" = "audio/mp4"
        ".mp3" = "audio/mpeg"
        ".wav" = "audio/wav"
        ".aac" = "audio/aac"
        ".flac" = "audio/flac"
        ".ogg" = "audio/ogg"
        ".opus" = "audio/ogg"
        ".mp4" = "video/mp4"
    }
    $extension = $audio.Extension.ToLowerInvariant()
    if (-not $contentTypes.ContainsKey($extension)) { throw "Unsupported smoke-audio extension." }
    $contentType = $contentTypes[$extension]

    $upload = Invoke-AppJson -Path "/api/uploads" -Method Post -Headers $csrfHeaders -Body @{
        clientRequestId = [Guid]::NewGuid().ToString()
        filename = $audio.Name
        contentType = $contentType
        sizeBytes = $audio.Length
        language = $Language
    }
    $fileStream = [System.IO.File]::OpenRead($audio.FullName)
    try {
        while (@($upload.confirmedParts).Count -lt [int]$upload.partCount) {
            $confirmedNumbers = @{}
            foreach ($part in @($upload.confirmedParts)) {
                $confirmedNumbers[[int]$part.partNumber] = $true
            }
            $missing = @(1..([int]$upload.partCount) | Where-Object {
                -not $confirmedNumbers.ContainsKey($_)
            } | Select-Object -First 3)
            $authorized = Invoke-AppJson `
                -Path "/api/uploads/$($upload.uploadSessionId)/parts/authorize" `
                -Method Post `
                -Headers $csrfHeaders `
                -Body @{ partNumbers = $missing }

            foreach ($part in @($authorized.authorizedParts)) {
                $offset = ([int64]$part.partNumber - 1) * [int64]$upload.partSizeBytes
                $remaining = [int64]$audio.Length - $offset
                $length = [int][Math]::Min([int64]$upload.partSizeBytes, $remaining)
                $buffer = [byte[]]::new($length)
                $fileStream.Position = $offset
                $read = 0
                while ($read -lt $length) {
                    $count = $fileStream.Read($buffer, $read, $length - $read)
                    if ($count -le 0) { throw "The smoke audio ended before its declared size." }
                    $read += $count
                }
                $partResponse = $null
                $partContent = New-Object System.Net.Http.ByteArrayContent -ArgumentList (,$buffer)
                try {
                    $partContent.Headers.ContentType =
                        [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse($contentType)
                    $partResponse = $httpClient.PutAsync(
                        [Uri]$part.url,
                        $partContent
                    ).GetAwaiter().GetResult()
                    if (-not $partResponse.IsSuccessStatusCode) {
                        throw "A restart-safe upload part was rejected by private storage."
                    }
                } finally {
                    $partContent.Dispose()
                    if ($null -ne $partResponse) { $partResponse.Dispose() }
                }
            }
            $upload = Invoke-AppJson -Path "/api/uploads/$($upload.uploadSessionId)"
            Write-Output "Uploaded $(@($upload.confirmedParts).Count) of $($upload.partCount) restart-safe part(s)."
        }
    } finally {
        $fileStream.Dispose()
    }

    $queued = Invoke-AppJson `
        -Path "/api/uploads/$($upload.uploadSessionId)/complete" `
        -Method Post `
        -Headers $csrfHeaders
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastStatus = ""
    do {
        $recording = Invoke-AppJson -Path "/api/recordings/$($queued.recordingId)"
        if ($recording.status -ne $lastStatus) {
            Write-Output "Railway processing status: $($recording.status) ($($recording.completedChunks)/$($recording.totalChunks) parts)."
            $lastStatus = $recording.status
        }
        if ($recording.status -eq "failed") {
            throw "Transcription failed with safe code: $($recording.safeErrorCode)"
        }
        if ($recording.status -eq "completed") { break }
        if ([DateTimeOffset]::UtcNow -ge $deadline) { throw "Timed out waiting for transcription." }
        Start-Sleep -Seconds 10
    } while ($true)

    $displayed = [string](Invoke-RestMethod `
        -Uri "$BaseUrl/api/recordings/$($queued.recordingId)/transcript" `
        -WebSession $webSession)
    $downloaded = [string](Invoke-RestMethod `
        -Uri "$BaseUrl/api/recordings/$($queued.recordingId)/transcript.txt" `
        -WebSession $webSession)
    if ($displayed -cne $downloaded) { throw "Displayed and downloaded transcript text differ." }
    $playback = Invoke-AppJson -Path "/api/recordings/$($queued.recordingId)/playback"
    $playbackResponse = $null
    $playbackRequest = New-Object System.Net.Http.HttpRequestMessage -ArgumentList (
        [System.Net.Http.HttpMethod]::Get,
        [Uri]$playback.url
    )
    $playbackRequest.Headers.TryAddWithoutValidation("Range", "bytes=0-0") | Out-Null
    try {
        $playbackResponse = $httpClient.SendAsync($playbackRequest).GetAwaiter().GetResult()
        if (-not $playbackResponse.IsSuccessStatusCode) {
            throw "The generated playback audio could not be read."
        }
    } finally {
        $playbackRequest.Dispose()
        if ($null -ne $playbackResponse) { $playbackResponse.Dispose() }
    }
    Invoke-AppJson `
        -Path "/api/recordings/$($queued.recordingId)" `
        -Method Delete `
        -Headers $csrfHeaders | Out-Null
    $deletionDeadline = [DateTimeOffset]::UtcNow.AddMinutes(10)
    do {
        try {
            Invoke-AppJson -Path "/api/recordings/$($queued.recordingId)" | Out-Null
        } catch {
            if ([int]$_.Exception.Response.StatusCode -eq 404) {
                $recordingDeleted = $true
                break
            }
            throw
        }
        if ([DateTimeOffset]::UtcNow -ge $deletionDeadline) {
            throw "Timed out waiting for smoke recording deletion."
        }
        Start-Sleep -Seconds 5
    } while ($true)
    Write-Output "Full Railway smoke transcription and cleanup passed without printing private transcript or signed URL data."
} finally {
    if ($null -ne $upload -and $null -eq $queued -and $null -ne $csrfHeaders) {
        try {
            Invoke-AppJson `
                -Path "/api/uploads/$($upload.uploadSessionId)/abort" `
                -Method Post `
                -Headers $csrfHeaders | Out-Null
        } catch {
            Write-Warning "The incomplete smoke upload could not be aborted automatically."
        }
    }
    if ($null -ne $queued -and -not $recordingDeleted -and $null -ne $csrfHeaders) {
        try {
            Invoke-AppJson `
                -Path "/api/recordings/$($queued.recordingId)" `
                -Method Delete `
                -Headers $csrfHeaders | Out-Null
        } catch {
            Write-Warning "The smoke recording could not be queued for deletion automatically."
        }
    }
    if ($null -ne $csrfHeaders) {
        try {
            Invoke-AppJson -Path "/api/auth/logout" -Method Post -Headers $csrfHeaders | Out-Null
        } catch {
            Write-Warning "The smoke session could not be closed automatically."
        }
    }
    $httpClient.Dispose()
}
