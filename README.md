# Endüstriyel Otoklav & Fikse Makinesi Canlı Kontrol Paneli

Bu proje, endüstriyel otoklav ve fikse makineleri için geliştirilmiş, gerçek zamanlı çift eksenli (Dual Axis) sıcaklık & basınç grafiği, anlık makine durum kartları ve akıllı arıza süre takip motoru (Alarm Engine) içeren bir SCADA/Web paneli çözümüdür.

---

## 🚀 Mimari & Teknolojiler
- **Backend:** Python 3.11+, FastAPI, Uvicorn, SQLite (Dahili & WAL Modu).
- **Frontend:** Chart.js, Vanilla CSS & Modern Dark Industrial UI, JetBrains Mono & Outfit tipografisi.
- **Dağıtım:** Docker & Docker Compose (`Port: 8080:8080`).

---

## 📌 API Endpoint'leri

### 1. `POST /api/telemetry`
Makine PLC'sinden veya MQTT ingestor servisinden gelen telemetri verisini işler.
```json
{
  "sicaklik": 120.5,
  "basinc": -0.85,
  "program_no": 1,
  "adim_adi": "VAKUM",
  "arizalar": {
    "Vakum Motor Arıza": false,
    "Kapak Motor Arıza": false,
    "Max Basınç Alarmı": false,
    "Acil Stop": false
  }
}
```

### 2. `GET /api/live`
En son kaydedilen telemetri durumunu, anlık aktif arızaları ve grafik için zaman serisi geçmişini döner.

### 3. `GET /api/alarms`
Veritabanındaki (`alarm_logs`) tüm geçmiş ve aktif arızaları listeler. Aktif arızaların geçen süresi anlık olarak dinamik hesaplanır.

### 4. `GET /`
Web Dashboard arayüzünü sunar.

---

## ⚙️ Arıza Takip & Süre Mantığı (Alarm Engine)
1. Backend hafızasında arızaların önceki durumları (`True` / `False`) izlenir.
2. **False ➔ True:** Yeni bir arıza başladığında SQLite `alarm_logs` tablosuna `(ariza_adi, baslangic_zamani, durum='Aktif')` kaydı açılır.
3. **True ➔ False:** Arıza giderildiğinde aktif kayıt bulunur, `bitis_zamani` ve `toplam_sure` (`dakika:saniye`, örn: `02:15`) güncellenir ve durumu `'Çözüldü'` yapılır.

---

## 🛠️ Kurulum & Çalıştırma (Karşı Bilgisayarda)

### Yöntem 1: Windows için Tek Tıkla Kurulum
Karşı bilgisayarda Windows Güvenlik Duvarı'nın portları (1883 MQTT ve 8080 Web) engellemesini önlemek ve Docker'ı tek tıkla ayağa kaldırmak için:
* **`baslat.bat`** dosyasına sağ tıklayıp **Yönetici olarak çalıştır** deyin (veya çift tıklayın).
* Bu betik:
  1. Windows Güvenlik Duvarı'nda **1883 (MQTT)**, **9001 (WS)** ve **8080 (Web)** portlarını otomatik olarak açar.
  2. `docker compose up -d --build` komutunu çalıştırarak hem Mosquitto hem de Web panelini ayağa kaldırır.
  3. HMI ekranına girmeniz gereken bilgisayar yerel IP adreslerini ekranda listeler.

### Yöntem 2: Linux Cihazlar için Kurulum (`baslat.sh`)
Linux işletim sistemli sunucu veya endüstriyel PC'lerde:
```bash
chmod +x baslat.sh
sudo ./baslat.sh
```
* Bu betik:
  1. Linux güvenlik duvarlarını (**UFW** veya **Firewalld**) kontrol eder; `1883`, `8080` ve `9001` portlarını otomatik açar.
  2. Docker ve Docker Compose servislerinin durumunu kontrol eder.
  3. Konteynerleri (`docker compose up -d --build`) ayağa kaldırır.
  4. HMI ekranı için cihazın yerel ağ IP adreslerini listeler.

### Yöntem 3: Manuel Docker Compose ile
```bash
docker compose up --build -d
```
Dashboard: `http://localhost:8080`

### Yöntem 4: Python ile Doğrudan Çalıştırma
```bash
# Bağımlılıkları yükleyin
pip install -r requirements.txt

# Uygulamayı başlatın
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```
