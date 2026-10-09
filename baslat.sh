#!/bin/bash
# ==============================================================
# Endüstriyel Otoklav & Fikse Makinesi Linux Başlatıcı
# ==============================================================

set -e

# Renkler
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}==============================================================${NC}"
echo -e "${CYAN}  OTOKLAV SCADA & MQTT LINUX KURULUM VE BASLATMA${NC}"
echo -e "${CYAN}==============================================================${NC}"

# 1. Sudo / Root Uyarısı
if [ "$EUID" -ne 0 ]; then
  echo -e "${YELLOW}[!] Güvenlik duvarı ve Docker izinleri için 'sudo' ile çalıştırmanız önerilir.${NC}"
  echo -e "${YELLOW}    Örnek kullanım: sudo ./baslat.sh${NC}"
fi

# 2. Güvenlik Duvarı Portlarını Açma (UFW veya Firewalld)
echo -e "\n${CYAN}[*] Güvenlik Duvarı Portları Kontrol Ediliyor...${NC}"

# UFW (Ubuntu / Debian)
if command -v ufw >/dev/null 2>&1; then
  UFW_STATUS=$(ufw status 2>/dev/null | head -n 1 || true)
  if [[ "$UFW_STATUS" == *"active"* ]]; then
    echo -e "${YELLOW}[*] UFW Güvenlik Duvarı devrede. 1883 ve 8080 portları açılıyor...${NC}"
    ufw allow 1883/tcp comment "Otoklav MQTT Broker" >/dev/null 2>&1 || true
    ufw allow 8080/tcp comment "Otoklav Web Dashboard" >/dev/null 2>&1 || true
    ufw allow 9001/tcp comment "Otoklav MQTT WS" >/dev/null 2>&1 || true
    echo -e "${GREEN}[ OK ] UFW port kuralları güncellendi (1883, 8080, 9001).${NC}"
  else
    echo -e "${GREEN}[ OK ] UFW kurulu ancak pasif.${NC}"
  fi
fi

# Firewalld (CentOS / RHEL / Fedora / Rocky Linux / AlmaLinux)
if command -v firewall-cmd >/dev/null 2>&1; then
  if systemctl is-active --quiet firewalld 2>/dev/null; then
    echo -e "${YELLOW}[*] Firewalld servisi aktif. 1883 ve 8080 portları açılıyor...${NC}"
    firewall-cmd --permanent --add-port=1883/tcp >/dev/null 2>&1 || true
    firewall-cmd --permanent --add-port=8080/tcp >/dev/null 2>&1 || true
    firewall-cmd --permanent --add-port=9001/tcp >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    echo -e "${GREEN}[ OK ] Firewalld kuralları kalıcı olarak eklendi (1883, 8080).${NC}"
  else
    echo -e "${GREEN}[ OK ] Firewalld pasif.${NC}"
  fi
fi

# iptables kuralı (Ek güvence)
if command -v iptables >/dev/null 2>&1 && [ "$EUID" -eq 0 ]; then
  iptables -I INPUT -p tcp --dport 1883 -j ACCEPT 2>/dev/null || true
  iptables -I INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true
fi

# 3. Docker Kontrolü
echo -e "\n${CYAN}[*] Docker Durumu Doğrulanıyor...${NC}"
if ! command -v docker >/dev/null 2>&1; then
  echo -e "${RED}[ HATA ] Docker kurulu bulunamadı! Lütfen önce Docker Engine kurun.${NC}"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo -e "${RED}[ HATA ] Docker servisi çalışmıyor veya yetki yetersiz!${NC}"
  echo -e "${YELLOW}  Çözüm: sudo systemctl start docker veya 'sudo ./baslat.sh' çalıştırın.${NC}"
  exit 1
fi
echo -e "${GREEN}[ OK ] Docker servisi aktif ve yanıt veriyor.${NC}"

# Docker Compose Komut Tespiti
DOCKER_COMPOSE_CMD=""
if docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DOCKER_COMPOSE_CMD="docker-compose"
else
  echo -e "${RED}[ HATA ] 'docker compose' eklentisi veya 'docker-compose' aracı bulunamadı!${NC}"
  exit 1
fi

# 4. Konteynerleri Başlatma
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "\n${CYAN}[*] Konteynerler derleniyor ve başlatılıyor...${NC}"
$DOCKER_COMPOSE_CMD up -d --build

echo -e "${GREEN}[ OK ] Konteynerler başarıyla ayağa kaldırıldı.${NC}"

# 5. Operatör Bilgilendirme Ekranı
echo -e "\n${CYAN}--------------------------------------------------------------${NC}"
echo -e "${GREEN}  OTOKLAV SİSTEMİ CANLI VE ÇALIŞIYOR${NC}"
echo -e "${CYAN}--------------------------------------------------------------${NC}"
echo -e "  Web Dashboard : http://localhost:8080"
echo -e "  MQTT Broker   : Port 1883 (TCP)"
echo -e ""
echo -e "${YELLOW}  HMI Ekranında veya Tarayıcıda Kullanabileceğiniz IP Adresleri:${NC}"

if command -v hostname >/dev/null 2>&1; then
  IPS=$(hostname -I 2>/dev/null || true)
  for ip in $IPS; do
    # 127.0.0.1 ve Docker bridge IP'lerini filtrele
    if [[ ! "$ip" =~ ^127\. && ! "$ip" =~ ^172\.1[7-9]\. && ! "$ip" =~ ^172\.2[0-9]\. ]]; then
      echo -e "   -> ${CYAN}$ip${NC}  (Web: http://${ip}:8080 | MQTT: ${ip}:1883)"
    fi
  done
elif command -v ip >/dev/null 2>&1; then
  ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | while read -r ip; do
    if [[ ! "$ip" =~ ^127\. && ! "$ip" =~ ^172\.1[7-9]\. && ! "$ip" =~ ^172\.2[0-9]\. ]]; then
      echo -e "   -> ${CYAN}$ip${NC}  (Web: http://${ip}:8080 | MQTT: ${ip}:1883)"
    fi
  done
fi

echo -e "${CYAN}--------------------------------------------------------------${NC}\n"
