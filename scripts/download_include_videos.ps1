param(
    [string]$Destination = "C:\AIAssets\INCLUDE_videos",
    [switch]$Download
)
$ErrorActionPreference = "Stop"
$recordUrl = "https://zenodo.org/api/records/4010759"
Write-Host "Official INCLUDE dataset video archive inventory" -ForegroundColor Cyan
Write-Host "Destination: $Destination"
Write-Host "Source: $recordUrl"
$record = Invoke-RestMethod -Uri $recordUrl -Method Get -TimeoutSec 60
$files = @($record.files | Where-Object { $_.key -match '\.zip$' })
if ($files.Count -eq 0) { throw "No ZIP archives were listed by the Zenodo record. Check the official dataset page manually." }
$total = [double](($files | Measure-Object -Property size -Sum).Sum)
Write-Host ""
$files | ForEach-Object {
    [PSCustomObject]@{ Archive = $_.key; SizeGiB = [math]::Round(([double]$_.size / 1GB), 2); DownloadURL = $_.links.self }
} | Format-Table -AutoSize
Write-Host ("Total listed ZIP size: {0:N2} GiB across {1} archive(s)." -f ($total / 1GB), $files.Count) -ForegroundColor Yellow
if (-not $Download) {
    Write-Host "Inventory only. Review archive sizes first. To download all listed ZIPs, re-run with -Download." -ForegroundColor Yellow
    exit 0
}
Write-Host "This will download ALL listed archives, not just the 50 classes. Confirm you have enough disk space." -ForegroundColor Yellow
$answer = Read-Host "Type DOWNLOAD to continue"
if ($answer -cne "DOWNLOAD") { Write-Host "Cancelled; no files downloaded."; exit 0 }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
foreach ($file in $files) {
    $target = Join-Path $Destination $file.key
    if (Test-Path $target) { Write-Host "Already exists, skipping: $($file.key)"; continue }
    Write-Host "Downloading $($file.key) ..."
    Invoke-WebRequest -Uri $file.links.self -OutFile $target -TimeoutSec 0
}
foreach ($archive in Get-ChildItem -Path $Destination -Filter *.zip -File) {
    $extractDir = Join-Path $Destination ([IO.Path]::GetFileNameWithoutExtension($archive.Name))
    if (Test-Path $extractDir) { Write-Host "Already extracted, skipping: $($archive.Name)"; continue }
    Write-Host "Extracting $($archive.Name) ..."
    Expand-Archive -LiteralPath $archive.FullName -DestinationPath $extractDir -Force
}
Write-Host "Dataset archives downloaded and extracted. Configure ISL_REFERENCE_VIDEO_DIR to the directory that contains the extracted category folders." -ForegroundColor Green
