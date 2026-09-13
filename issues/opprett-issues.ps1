<#
    Oppretter ett GitHub-issue per .md-fil i denne mappa, i filnavnrekkefølge.

    Forutsetter at "gh auth login" er kjørt og at du står i et repo med en
    GitHub-remote. Kjør fra prosjektroten:

        .\issues\opprett-issues.ps1 -TorrKjoring     # vis hva som ville skjedd
        .\issues\opprett-issues.ps1                  # opprett på ekte

    Kroppene viser til hverandre med tittel og ikke med #-nummer, så
    rekkefølgen betyr ingenting og skriptet kan kjøres mot et repo som
    allerede har issues.
#>

[CmdletBinding()]
param(
    [switch]$TorrKjoring
)

$ErrorActionPreference = 'Stop'
$mappe = $PSScriptRoot

if (-not $TorrKjoring -and -not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "gh finnes ikke i PATH. Installer GitHub CLI: https://cli.github.com"
}

# Etikettene som brukes i front matter. Opprettes hvis de mangler.
$etiketter = @{
    'bug'           = @('d73a4a', 'Noe er galt')
    'enhancement'   = @('a2eeef', 'Ny funksjonalitet')
    'data'          = @('0e8a16', 'Datakvalitet og kilder')
    'frontend'      = @('c5def5', 'Kart, lag og grensesnitt')
    'sikkerhet'     = @('b60205', 'Sikkerhet')
    'drift'         = @('5319e7', 'Drift, utrulling og overvåking')
    'opprydding'    = @('fef2c0', 'Teknisk gjeld')
    'undersokelse'  = @('d4c5f9', 'Måling mangler, ikke kode')
    'verifisering'  = @('bfd4f2', 'Skal kjøres og bekreftes')
    'prioritet-hoy' = @('e99695', 'Tas først')
}

foreach ($navn in $etiketter.Keys) {
    $farge = $etiketter[$navn][0]
    $tekst = $etiketter[$navn][1]
    if ($TorrKjoring) {
        Write-Host "[tørr] etikett: $navn"
        continue
    }
    # --force gjør kallet idempotent: oppretter eller oppdaterer.
    gh label create $navn --color $farge --description $tekst --force | Out-Null
}

$filer = Get-ChildItem -Path $mappe -Filter '*.md' |
         Where-Object { $_.Name -match '^\d{2}-' } |
         Sort-Object Name

foreach ($fil in $filer) {
    $linjer = Get-Content -Path $fil.FullName -Encoding utf8

    if ($linjer[0] -ne '---') {
        Write-Warning "$($fil.Name): mangler front matter, hoppet over"
        continue
    }

    $slutt = 1
    while ($slutt -lt $linjer.Count -and $linjer[$slutt] -ne '---') { $slutt++ }

    $tittel = $null
    $merker = @()
    for ($i = 1; $i -lt $slutt; $i++) {
        if ($linjer[$i] -match '^title:\s*"?(.+?)"?\s*$')  { $tittel = $Matches[1] }
        if ($linjer[$i] -match '^labels:\s*(.+)$') {
            $merker = $Matches[1] -split ',' | ForEach-Object { $_.Trim() }
        }
    }

    if (-not $tittel) {
        Write-Warning "$($fil.Name): ingen title, hoppet over"
        continue
    }

    $kropp = ($linjer[($slutt + 1)..($linjer.Count - 1)] -join "`n").Trim()

    if ($TorrKjoring) {
        Write-Host "[tørr] $($fil.Name)"
        Write-Host "       tittel:  $tittel"
        Write-Host "       merker:  $($merker -join ', ')"
        Write-Host "       kropp:   $($kropp.Length) tegn"
        continue
    }

    $ghArgs = @('issue', 'create', '--title', $tittel, '--body', $kropp)
    foreach ($m in $merker) { $ghArgs += @('--label', $m) }

    $url = & gh @ghArgs
    if ($LASTEXITCODE -ne 0) { throw "gh issue create feilet for $($fil.Name)" }
    Write-Host "$($fil.Name) -> $url"
}

Write-Host ""
Write-Host "Ferdig. Slett denne mappa når issuene står i GitHub -"
Write-Host "to steder å vedlikeholde er ett for mye."
