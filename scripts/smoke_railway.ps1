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

if (-not $BaseUrl -or -not $Username -or -not $Pin) {
    throw "Set TRANSCRIBER_SMOKE_URL, TRANSCRIBER_SMOKE_USERNAME, and TRANSCRIBER_SMOKE_PIN."
}
$BaseUrl = $BaseUrl.TrimEnd("/")
$baseUri = [Uri]$BaseUrl
if ($baseUri.Scheme -ne "https" -and $baseUri.Host -notin @("localhost", "127.0.0.1")) {
    throw "The smoke URL must use HTTPS."
}
$origin = "$($baseUri.Scheme)://$($baseUri.Authority)"
$webSession = $null
$csrfHeaders = $null

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

    $login = Invoke-AppJson -Path "/api/auth/login" -Method Post -Body @{
        username = $Username
        pin = $Pin
    }
    $csrfHeaders = @{
        Origin = $origin
        "X-CSRF-Token" = $login.csrfToken
    }
    $recordings = @(Invoke-AppJson -Path "/api/recordings")
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
                Invoke-WebRequest `
                    -Uri $part.url `
                    -Method Put `
                    -ContentType $contentType `
                    -Body $buffer | Out-Null
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

    $displayed = (Invoke-WebRequest `
        -Uri "$BaseUrl/api/recordings/$($queued.recordingId)/transcript" `
        -WebSession $webSession).Content
    $downloaded = (Invoke-WebRequest `
        -Uri "$BaseUrl/api/recordings/$($queued.recordingId)/transcript.txt" `
        -WebSession $webSession).Content
    if ($displayed -cne $downloaded) { throw "Displayed and downloaded transcript text differ." }
    $playback = Invoke-AppJson -Path "/api/recordings/$($queued.recordingId)/playback"
    Invoke-WebRequest -Uri $playback.url -Headers @{ Range = "bytes=0-0" } | Out-Null
    Write-Output "Full Railway smoke transcription passed without printing private transcript or signed URL data."
} finally {
    if ($null -ne $csrfHeaders) {
        try {
            Invoke-AppJson -Path "/api/auth/logout" -Method Post -Headers $csrfHeaders | Out-Null
        } catch {
            Write-Warning "The smoke session could not be closed automatically."
        }
    }
}
