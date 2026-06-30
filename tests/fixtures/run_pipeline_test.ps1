# Full pipeline test — CSV, KML, KMZ
# Run from project root: powershell -File tests\fixtures\run_pipeline_test.ps1

$BASE = "http://localhost:8000"
$PROJ = "C:\Users\Admin\ftth_data_ingestion_project"

# ── Login ────────────────────────────────────────────────────────────────────
Write-Host "`n=== FTTH Pipeline End-to-End Test ===" -ForegroundColor Cyan
$resp = Invoke-RestMethod "$BASE/api/login" -Method POST `
    -Body '{"username":"admin","password":"Meridian@2026"}' `
    -ContentType "application/json"
$tok = $resp.token
$H = @{Authorization="Bearer $tok"}
Write-Host "Logged in as: $($resp.username)" -ForegroundColor Green

# ── Upload helper ─────────────────────────────────────────────────────────────
function Upload-File($filePath, $customerId) {
    $boundary = [System.Guid]::NewGuid().ToString()
    $fileName = [System.IO.Path]::GetFileName($filePath)
    $fileBytes = [System.IO.File]::ReadAllBytes($filePath)
    $nl = "`r`n"
    $bodyLines = @(
        "--$boundary",
        "Content-Disposition: form-data; name=`"customer_id`"",
        "",
        $customerId,
        "--$boundary",
        "Content-Disposition: form-data; name=`"file`"; filename=`"$fileName`"",
        "Content-Type: application/octet-stream",
        ""
    )
    $headerBytes = [System.Text.Encoding]::UTF8.GetBytes(($bodyLines -join $nl) + $nl)
    $footerBytes = [System.Text.Encoding]::UTF8.GetBytes("$nl--$boundary--$nl")
    $body = New-Object byte[] ($headerBytes.Length + $fileBytes.Length + $footerBytes.Length)
    [System.Buffer]::BlockCopy($headerBytes, 0, $body, 0, $headerBytes.Length)
    [System.Buffer]::BlockCopy($fileBytes, 0, $body, $headerBytes.Length, $fileBytes.Length)
    [System.Buffer]::BlockCopy($footerBytes, 0, $body, $headerBytes.Length + $fileBytes.Length, $footerBytes.Length)
    return Invoke-RestMethod "$BASE/api/upload" -Method POST -Body $body `
        -ContentType "multipart/form-data; boundary=$boundary" -Headers $H
}

# ── Process + wait helper ─────────────────────────────────────────────────────
function Process-And-Wait($jobId, $label, $timeoutSec=300) {
    Write-Host "`n--- Starting pipeline for $label (job $jobId) ---" -ForegroundColor Yellow
    try {
        Invoke-RestMethod "$BASE/api/jobs/$jobId/process" -Method POST -Headers $H | Out-Null
    } catch { Write-Host "  Process start error: $_" -ForegroundColor Red; return $false }

    $elapsed = 0
    $interval = 10
    while ($elapsed -lt $timeoutSec) {
        Start-Sleep $interval
        $elapsed += $interval
        try {
            $p = Invoke-RestMethod "$BASE/api/jobs/$jobId/agents" -Headers $H
            $overall = $p.overall_progress
            $status  = $p.status
            Write-Host "  [$([int]$elapsed)s] status=$status progress=$overall%"
            if ($status -eq "completed" -or $status -eq "failed") { break }
        } catch { Write-Host "  Poll error: $_"; break }
    }
    $p = Invoke-RestMethod "$BASE/api/jobs/$jobId/agents" -Headers $H
    Write-Host "  Final status: $($p.status)  progress: $($p.overall_progress)%"
    foreach ($ag in $p.agents) {
        $sym = if ($ag.status -eq "completed") { "OK" } elseif ($ag.status -eq "failed") { "FAIL" } else { "  " }
        Write-Host "    [$sym] $($ag.agent_name): $($ag.status) processed=$($ag.records_processed)/$($ag.records_total)"
    }
    return $p.status -eq "completed"
}

# ── Check results helper ──────────────────────────────────────────────────────
function Check-Results($jobId, $label) {
    Write-Host "`n--- Results for $label ---" -ForegroundColor Cyan
    $recs = Invoke-RestMethod "$BASE/api/records?job_id=$jobId&limit=10" -Headers $H
    Write-Host "  Total records: $($recs.total)"
    Write-Host "  Columns: $($recs.columns.Count)"
    $agentCols = $recs.columns | Where-Object { $_.source -match "agent" }
    Write-Host "  Agent columns: $($agentCols.Count) ($($agentCols.source | Sort-Object -Unique -join ','))"

    foreach ($rec in $recs.records | Select-Object -First 3) {
        $m = $rec.raw_data
        $a1  = $m.agent1_validation_status
        $a2  = $m.agent2_status
        $a3  = $m.agent3_status
        $a4  = $m.agent4_structure_type
        $a5  = $m.agent5_structure_type
        $a6p = $m.agent6_ftth_priority
        $a6s = $m.agent6_final_structure_type
        Write-Host "  Row $($rec.source_row_number): addr='$($rec.raw_address)' lat=$($rec.latitude)"
        Write-Host "    A1=$a1  A2=$a2  A3=$a3  A4=$a4  A5=$a5  A6Priority=$a6p A6Type=$a6s"
    }

    # Check geo
    $geo = Invoke-RestMethod "$BASE/api/records/geo?job_id=$jobId" -Headers $H
    $withFtth = ($geo.features | Where-Object { $_.ftth_priority -ne "" }).Count
    Write-Host "  Geo features: $($geo.count)  with FTTH priority: $withFtth"
}

# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: CSV
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n════ TEST 1: CSV ════" -ForegroundColor Magenta
$csvR = Upload-File "$PROJ\tests\fixtures\test_pipeline.csv" "test_csv_pipeline"
$csvJob = $csvR.job_id
Write-Host "Uploaded: job=$csvJob  rows=$($csvR.row_count)  status=$($csvR.status)"

$recs = Invoke-RestMethod "$BASE/api/records?job_id=$csvJob&limit=5" -Headers $H
Write-Host "Records ingested: $($recs.total)"
$recs.records | ForEach-Object { Write-Host "  row=$($_.source_row_number) addr='$($_.raw_address)' lat=$($_.latitude) lon=$($_.longitude)" }

$ok1 = Process-And-Wait $csvJob "CSV" 600
if ($ok1) { Check-Results $csvJob "CSV" }

# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: KML
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n════ TEST 2: KML ════" -ForegroundColor Magenta
$kmlR = Upload-File "$PROJ\tests\fixtures\test_pipeline.kml" "test_kml_pipeline"
$kmlJob = $kmlR.job_id
Write-Host "Uploaded: job=$kmlJob  rows=$($kmlR.row_count)  status=$($kmlR.status)"

$recs = Invoke-RestMethod "$BASE/api/records?job_id=$kmlJob&limit=5" -Headers $H
Write-Host "Records ingested: $($recs.total)"
$recs.records | ForEach-Object { Write-Host "  row=$($_.source_row_number) addr='$($_.raw_address)' lat=$($_.latitude) lon=$($_.longitude)" }

$ok2 = Process-And-Wait $kmlJob "KML" 600
if ($ok2) { Check-Results $kmlJob "KML" }

# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: KMZ (small sample — first 5 rows only via 728-row real file)
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n════ TEST 3: KMZ ════" -ForegroundColor Magenta
$kmzPath = "$PROJ\SEFH16756 - EWR24675 LEXINGTON\SEFH16756 - EWR24675 LEXINGTON\SEFH16756 - EWR24675 LEXINGTON.kmz"
$kmzR = Upload-File $kmzPath "test_kmz_pipeline"
$kmzJob = $kmzR.job_id
Write-Host "Uploaded: job=$kmzJob  rows=$($kmzR.row_count)  status=$($kmzR.status)"

$recs = Invoke-RestMethod "$BASE/api/records?job_id=$kmzJob&limit=5" -Headers $H
Write-Host "Records ingested: $($recs.total)"
$recs.records | Select-Object -First 3 | ForEach-Object { Write-Host "  row=$($_.source_row_number) addr='$($_.raw_address)' lat=$($_.latitude) lon=$($_.longitude)" }

# KMZ has 728 rows — just run and check first few results
$ok3 = Process-And-Wait $kmzJob "KMZ" 1200
if ($ok3) { Check-Results $kmzJob "KMZ" }

Write-Host "`n=== Test Summary ===" -ForegroundColor Cyan
Write-Host "CSV:  $(if($ok1){'PASS'}else{'FAIL'})"
Write-Host "KML:  $(if($ok2){'PASS'}else{'FAIL'})"
Write-Host "KMZ:  $(if($ok3){'PASS'}else{'FAIL'})"
