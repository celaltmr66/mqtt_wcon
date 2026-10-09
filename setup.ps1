<#
    Endüstriyel Otoklav & Fikse Makinesi Canlı Kontrol Paneli Kurulum ve Başlatma Betiği
    Bu betik:
    1. Yönetici haklarını doğrular (gerekirse otomatik yetki ister).
    2. Windows Güvenlik Duvarında 1883 (MQTT), 9001 (WS) ve 8080 (Web Dashboard) portlarını tüm ağ profillerine (Public/Private/Domain) AÇAR.
    3. Docker Compose ile sistemi derleyip arka planda başlatır.
    4. HMI'a girilmesi gereken yerel IP adreslerini ekrana listeler.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

# 1. Yönetici Yetkisi Kontrolü (UAC Yükseltme)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[*] Yönetici hakları gerekiyor. Yetki isteniyor..." -ForegroundColor Yellow
    Start-Process powershell.exe -ArgumentList ("-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"") -Verb RunAs
    exit
}

Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "  OTOKLAV KONTROL PANELI & MQTT KURULUM VE BASLATMA" -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

# 2. Güvenlik Duvarı Kurallarını Aç
function Add-FirewallRuleIfMissing {
    param($Name, $Port)
    if (Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue) {
        Write-Host "[ OK ] Güvenlik Duvarı: $Name (Port $Port) zaten açık." -ForegroundColor Green
    } else {
        try {
            New-NetFirewallRule -DisplayName $Name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
            Write-Host "[ + ] Güvenlik Duvarı Kuralı Eklendi: $Name (Port $Port - Tümü)" -ForegroundColor Green
        } catch {
            Write-Host "[WARN] Güvenlik duvarı kuralı eklenemedi: $_" -ForegroundColor Yellow
        }
    }
}

Add-FirewallRuleIfMissing -Name "Otoklav MQTT Broker (1883)" -Port 1883
Add-FirewallRuleIfMissing -Name "Otoklav Web Dashboard (8080)" -Port 8080
Add-FirewallRuleIfMissing -Name "Otoklav MQTT WebSocket (9001)" -Port 9001

# 3. Docker Kontrolü
try {
    docker info --format '{{.ServerVersion}}' | Out-Null
    Write-Host "[ OK ] Docker çalışıyor." -ForegroundColor Green
} catch {
    Write-Host "[ HATA ] Docker Desktop çalışmıyor! Lütfen Docker Desktop'ı başlatıp tekrar deneyin." -ForegroundColor Red
    pause
    exit 1
}

# 4. Docker Compose Başlatma
Write-Host "`n[*] Konteynerler derleniyor ve başlatılıyor..." -ForegroundColor Cyan
docker compose up -d --build

if ($LASTEXITCODE -eq 0) {
    Write-Host "[ OK ] Konteynerler başarıyla ayağa kaldırıldı." -ForegroundColor Green
} else {
    Write-Host "[ HATA ] Docker compose başlatılamadı!" -ForegroundColor Red
    pause
    exit 1
}

# 5. Operatör Bilgilendirmesi
$ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }

Write-Host "`n--------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "  SİSTEM CANLI VE ÇALIŞIYOR" -ForegroundColor Green
Write-Host "--------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "  Web Dashboard : http://localhost:8080" -ForegroundColor White
Write-Host "  MQTT Broker   : Port 1883 (Açık)" -ForegroundColor White
Write-Host ""
Write-Host "  HMI Ekranında Kullanabileceğiniz Bilgisayar IP Adresleri:" -ForegroundColor Yellow
foreach ($ip in $ips) {
    Write-Host ("   -> {0}  [{1}]" -f $ip.IPAddress, $ip.InterfaceAlias) -ForegroundColor Cyan
}
Write-Host "--------------------------------------------------------------`n" -ForegroundColor Cyan

Write-Host "Kurulum tamamlandı. Kapatmak için bir tuşa basın..."
pause
