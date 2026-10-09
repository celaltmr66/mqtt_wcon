import os
import json
import time
import sqlite3
import threading
from datetime import datetime
from collections import deque
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
# pyrefly: ignore [missing-import]
import paho.mqtt.client as mqtt

# -------------------------------------------------------------
# Yapılandırma ve Veritabanı
# -------------------------------------------------------------
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "autoclave.db"))
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

db_lock = threading.Lock()

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alarm_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ariza_adi TEXT NOT NULL,
                baslangic_zamani TEXT NOT NULL,
                bitis_zamani TEXT,
                toplam_sure TEXT,
                durum TEXT NOT NULL DEFAULT 'Aktif'
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zaman TEXT NOT NULL,
                sicaklik REAL NOT NULL,
                basinc REAL NOT NULL,
                program_no INTEGER NOT NULL,
                adim_adi TEXT NOT NULL
            );
        """)
        conn.commit()
        conn.close()

# -------------------------------------------------------------
# Alarm Engine & Bellek Yönetimi
# -------------------------------------------------------------
class AlarmEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_alarm_states: Dict[str, bool] = {}
        self.active_alarm_ids: Dict[str, int] = {}
        self.reload_active_from_db()

    def reload_active_from_db(self):
        """Sunucu başladığında veritabanındaki aktif arızaları hafızaya yükler."""
        with self.lock:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT id, ariza_adi FROM alarm_logs WHERE durum = 'Aktif'")
                rows = cursor.fetchall()
                for row in rows:
                    self.active_alarm_ids[row["ariza_adi"]] = row["id"]
                    self.last_alarm_states[row["ariza_adi"]] = True
                conn.close()
            except Exception as e:
                print(f"[AlarmEngine] Aktif alarmlar yüklenirken hata: {e}")

    def process_alarms(self, current_alarms: Dict[str, bool]):
        """
        Durum False -> True olduğunda alarm_logs tablosuna Aktif kaydı açar.
        Durum True -> False olduğunda bitis_zamani ve toplam_sure güncelleyip Çözüldü yapar.
        """
        with self.lock:
            now = datetime.now()
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")

            conn = get_db_connection()
            cursor = conn.cursor()

            for ariza_adi, current_state in current_alarms.items():
                prev_state = self.last_alarm_states.get(ariza_adi, False)

                # 1. Yeni Arıza Oluştu: False -> True
                if not prev_state and current_state:
                    cursor.execute(
                        "INSERT INTO alarm_logs (ariza_adi, baslangic_zamani, durum) VALUES (?, ?, 'Aktif')",
                        (ariza_adi, now_str)
                    )
                    new_id = cursor.lastrowid
                    self.active_alarm_ids[ariza_adi] = new_id
                    self.last_alarm_states[ariza_adi] = True
                    print(f"[AlarmEngine] 🚨 YENİ ARIZA: {ariza_adi} (ID: {new_id})")

                # 2. Arıza Çözüldü: True -> False
                elif prev_state and not current_state:
                    alarm_id = self.active_alarm_ids.get(ariza_adi)
                    if not alarm_id:
                        cursor.execute(
                            "SELECT id, baslangic_zamani FROM alarm_logs WHERE ariza_adi = ? AND durum = 'Aktif' ORDER BY id DESC LIMIT 1",
                            (ariza_adi,)
                        )
                        active_row = cursor.fetchone()
                        if active_row:
                            alarm_id = active_row["id"]
                            start_time_str = active_row["baslangic_zamani"]
                        else:
                            start_time_str = now_str
                    else:
                        cursor.execute("SELECT baslangic_zamani FROM alarm_logs WHERE id = ?", (alarm_id,))
                        row = cursor.fetchone()
                        start_time_str = row["baslangic_zamani"] if row else now_str

                    try:
                        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                        elapsed_seconds = max(0, int((now - start_dt).total_seconds()))
                    except Exception:
                        elapsed_seconds = 0

                    dakika = elapsed_seconds // 60
                    saniye = elapsed_seconds % 60
                    toplam_sure_str = f"{dakika:02d}:{saniye:02d}"

                    if alarm_id:
                        cursor.execute(
                            "UPDATE alarm_logs SET bitis_zamani = ?, toplam_sure = ?, durum = 'Çözüldü' WHERE id = ?",
                            (now_str, toplam_sure_str, alarm_id)
                        )
                        print(f"[AlarmEngine] ✅ ARIZA ÇÖZÜLDÜ: {ariza_adi} (Süre: {toplam_sure_str})")

                    self.active_alarm_ids.pop(ariza_adi, None)
                    self.last_alarm_states[ariza_adi] = False

                # 3. Durum Değişmedi (True -> True)
                elif current_state:
                    self.last_alarm_states[ariza_adi] = True
                    if ariza_adi not in self.active_alarm_ids:
                        cursor.execute(
                            "SELECT id FROM alarm_logs WHERE ariza_adi = ? AND durum = 'Aktif' ORDER BY id DESC LIMIT 1",
                            (ariza_adi,)
                        )
                        active_row = cursor.fetchone()
                        if active_row:
                            self.active_alarm_ids[ariza_adi] = active_row["id"]

            conn.commit()
            conn.close()

# -------------------------------------------------------------
# Canlı Veri Arabelleği & Modeller
# -------------------------------------------------------------
class TelemetryPayload(BaseModel):
    sicaklik: float = Field(..., description="Sıcaklık değeri (°C)")
    basinc: float = Field(..., description="Basınç değeri (Bar)")
    program_no: int = Field(..., description="Aktif Program Numarası")
    adim_adi: str = Field(..., description="Aktif Adım Adı (örn: VAKUM)")
    hmi_model: Optional[str] = Field(None, description="HMI Cihaz Modeli")
    hmi_ip: Optional[str] = Field(None, description="HMI Cihaz IP Adresi")

latest_telemetry = {
    "sicaklik": 0.0,
    "basinc": 0.0,
    "program_no": 0,
    "adim_adi": "ELLE KONTROL",
    "arizalar": {
        "Vakum Motor Arıza": False,
        "Kapak Motor Arıza": False,
        "Max Basınç Alarmı": False,
        "Acil Stop": False
    },
    "zaman": datetime.now().strftime("%H:%M:%S"),
    "tarih_saat": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "hmi_ip": "192.168.3.48",
    "hmi_model": "WECON HMI",
    "last_seen": None
}

telemetry_history_buffer = deque(maxlen=60)
telemetry_lock = threading.Lock()

# -------------------------------------------------------------
# Dahili MQTT Dinleyici (Wecon HMI Köprüsü)
# -------------------------------------------------------------
mqtt_client: Optional[mqtt.Client] = None

HMI_STEP_MAP = {
    0: "ELLE KONTROL",
    1: "VAKUM",
    2: "ÖN ISITMA",
    3: "BASINÇLI FİKSE",
    4: "SOĞUTMA & TAHLİYE"
}

def process_incoming_telemetry_dict(t: float, p: float, prog: int, adim: str, alarms: Dict[str, bool], model: Optional[str] = None, ip: Optional[str] = None):
    now = datetime.now()
    now_time_str = now.strftime("%H:%M:%S")
    now_full_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # 1. Alarm Engine'i çalıştır
    alarm_engine.process_alarms(alarms)

    # 2. Canlı belleği güncelle
    with telemetry_lock:
        latest_telemetry["sicaklik"] = t
        latest_telemetry["basinc"] = p
        latest_telemetry["program_no"] = prog
        latest_telemetry["adim_adi"] = adim
        latest_telemetry["arizalar"] = alarms
        latest_telemetry["zaman"] = now_time_str
        latest_telemetry["tarih_saat"] = now_full_str
        latest_telemetry["last_seen"] = now_full_str
        latest_telemetry["last_seen_monotonic"] = time.monotonic()
        if model:
            latest_telemetry["hmi_model"] = model
        if ip:
            latest_telemetry["hmi_ip"] = ip

        point = {
            "zaman": now_time_str,
            "sicaklik": t,
            "basinc": p,
            "program_no": prog,
            "adim_adi": adim
        }
        telemetry_history_buffer.append(point)

    # 3. SQLite geçmişine yaz
    try:
        with db_lock:
            conn = get_db_connection()
            conn.execute(
                "INSERT INTO telemetry_history (zaman, sicaklik, basinc, program_no, adim_adi) VALUES (?, ?, ?, ?, ?)",
                (now_full_str, t, p, prog, adim)
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[Telemetry] SQLite yazım hatası: {e}")

def on_mqtt_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[MQTT] ✅ Broker'a başarıyla bağlanıldı ({MQTT_HOST}:{MQTT_PORT}).")
        # Tüm olası konulara abone ol
        client.subscribe("#", qos=1)
        client.subscribe("wecon/#", qos=1)
        print("[MQTT] 📡 'wecon/#' ve '#' konuları dinleniyor...")
    else:
        print(f"[MQTT] ❌ Bağlantı hatası, kod: {reason_code}")

def on_mqtt_message(client, userdata, msg):
    try:
        payload_raw = msg.payload.decode("utf-8", errors="replace").strip()
        print(f"[MQTT Gelen] Konu: {msg.topic} -> {payload_raw}")

        try:
            doc = json.loads(payload_raw)
        except Exception:
            return

        if not isinstance(doc, dict):
            return

        # Değerleri ayıkla (HMI veya REST formatı)
        # Sıcaklık: temp, sicaklik, temperature
        t_raw = doc.get("temp", doc.get("sicaklik", doc.get("temperature")))
        # Basınç: pressure, basinc
        p_raw = doc.get("pressure", doc.get("basinc"))

        # Mevcut durumdan varsayılan al
        with telemetry_lock:
            cur_t = latest_telemetry["sicaklik"]
            cur_p = latest_telemetry["basinc"]
            cur_prog = latest_telemetry["program_no"]
            cur_adim = latest_telemetry["adim_adi"]
            cur_alarms = dict(latest_telemetry["arizalar"])

        # Sıcaklık dönüşümü (Wecon bazen 320 = 32.0 şeklinde gönderir)
        if t_raw is not None:
            try:
                t_float = float(t_raw)
                if t_float > 300: # Muhtemel x10 skalalı tam sayı
                    t_float = round(t_float / 10.0, 1)
                else:
                    t_float = round(t_float, 1)
            except Exception:
                t_float = cur_t
        else:
            t_float = cur_t

        # Basınç dönüşümü
        if p_raw is not None:
            try:
                p_float = round(float(p_raw), 2)
            except Exception:
                p_float = cur_p
        else:
            p_float = cur_p

        # Program No
        prog_raw = doc.get("program_no", doc.get("recipe_no", doc.get("prog", cur_prog)))
        try:
            prog_val = int(prog_raw)
        except Exception:
            prog_val = cur_prog

        # Adım / Faz
        step_raw = doc.get("adim_adi", doc.get("step_name", doc.get("step", doc.get("adim"))))
        if step_raw is not None:
            if isinstance(step_raw, int) or (isinstance(step_raw, str) and step_raw.isdigit()):
                step_idx = int(step_raw)
                adim_val = HMI_STEP_MAP.get(step_idx, f"ADIM #{step_idx}")
            else:
                adim_val = str(step_raw).upper()
        else:
            adim_val = cur_adim

        # Arızalar Haritası
        arizalar = dict(cur_alarms)
        if "arizalar" in doc and isinstance(doc["arizalar"], dict):
            arizalar.update(doc["arizalar"])
        else:
            # Wecon HMI Giriş bitleri kontrolü
            if "in_106_0" in doc:
                arizalar["Vakum Motor Arıza"] = bool(doc["in_106_0"])
            if "in_106_1" in doc:
                arizalar["Kapak Motor Arıza"] = bool(doc["in_106_1"])
            if "in_106_2" in doc or "in_108_0" in doc:
                arizalar["Acil Stop"] = bool(doc.get("in_106_2", doc.get("in_108_0")))
            if p_float > 3.5:
                arizalar["Max Basınç Alarmı"] = True

        # Cihaz Modeli & IP Tespiti
        detected_model = None
        topic_parts = msg.topic.strip("/").split("/")
        if len(topic_parts) >= 2 and topic_parts[0].lower() == "wecon":
            raw_model = topic_parts[1].upper()
            if "3070" in raw_model:
                detected_model = f"WECON {raw_model} (7\")"
            elif "3102" in raw_model:
                detected_model = f"WECON {raw_model} (10.2\")"
            elif "3043" in raw_model:
                detected_model = f"WECON {raw_model} (4.3\")"
            elif "8150" in raw_model or "9150" in raw_model:
                detected_model = f"WECON {raw_model} (15\")"
            else:
                detected_model = f"WECON {raw_model}"

        # JSON payload içinde model tanımlanmışsa öncelik ver
        for key in ["model", "device", "cihaz", "hmi_model", "cihaz_modeli"]:
            if key in doc and doc[key]:
                val = str(doc[key]).strip()
                if "3070" in val and "7" not in val:
                    detected_model = f"WECON {val} (7\")"
                elif "3102" in val and "10" not in val:
                    detected_model = f"WECON {val} (10.2\")"
                else:
                    detected_model = val
                break

        detected_ip = doc.get("ip") or doc.get("hmi_ip") or None

        process_incoming_telemetry_dict(t_float, p_float, prog_val, adim_val, arizalar, model=detected_model, ip=detected_ip)

    except Exception as exc:
        print(f"[MQTT] Mesaj işleme hatası: {exc}")

def start_mqtt_worker():
    global mqtt_client
    def _worker():
        global mqtt_client
        while True:
            try:
                print(f"[MQTT Worker] {MQTT_HOST}:{MQTT_PORT} adresine bağlanılıyor...")
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="autoclave-sub-engine")
                client.on_connect = on_mqtt_connect
                client.on_message = on_mqtt_message
                client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
                mqtt_client = client
                client.loop_forever()
            except Exception as e:
                print(f"[MQTT Worker] Bağlantı koptu veya broker hazır değil: {e}. 3 sn sonra tekrar denenecek...")
                time.sleep(3)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

# -------------------------------------------------------------
# FastAPI Yaşam Döngüsü & Uygulaması
# -------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    alarm_engine.reload_active_from_db()
    start_mqtt_worker()
    yield
    if mqtt_client:
        try:
            mqtt_client.disconnect()
        except Exception:
            pass

app = FastAPI(
    title="Otoklav / Fikse Makinesi Canlı Kontrol Paneli",
    description="Endüstriyel Otoklav için Sıcaklık, Basınç & Arıza Takip Sistemi",
    version="1.0.0",
    lifespan=lifespan
)

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
alarm_engine = AlarmEngine()

@app.get("/", response_class=HTMLResponse)
@app.get("/page1", response_class=HTMLResponse)
@app.get("/page2", response_class=HTMLResponse)
@app.get("/report", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Web Dashboard'unu sunar."""
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/status")
async def get_status():
    """HMI cihazının canlı bağlantı durumunu (online/offline) döner."""
    now_m = time.monotonic()
    with telemetry_lock:
        last_m = latest_telemetry.get("last_seen_monotonic")
        device_name = latest_telemetry.get("hmi_model", "WECON HMI")
        device_ip = latest_telemetry.get("hmi_ip", "192.168.3.48")
        if last_m is not None:
            elapsed = round(now_m - last_m, 1)
            online = elapsed < 5.0
        else:
            elapsed = None
            online = False
    return {
        "online": online,
        "elapsed_seconds": elapsed,
        "hmi_reachable": True,
        "device_name": device_name,
        "hmi_ip": device_ip,
        "broker": f"192.168.3.200:1883"
    }

@app.post("/api/telemetry")
async def post_telemetry(payload: TelemetryPayload):
    """
    PLC / MQTT Ingestor / REST tarafından gönderilen telemetri verisini işler.
    """
    process_incoming_telemetry_dict(
        payload.sicaklik,
        payload.basinc,
        payload.program_no,
        payload.adim_adi,
        payload.arizalar,
        model=payload.hmi_model,
        ip=payload.hmi_ip
    )
    active_count = len(alarm_engine.active_alarm_ids)
    return {
        "status": "success",
        "message": "Telemetri başarıyla işlendi",
        "aktif_ariza_sayisi": active_count
    }

@app.get("/api/live")
async def get_live():
    """Son gelen anlık verileri, arıza durumlarını ve son zaman serisini döner."""
    with telemetry_lock:
        cur_telemetry = dict(latest_telemetry)
        history_list = list(telemetry_history_buffer)

    active_alarms_list = []
    now = datetime.now()

    with alarm_engine.lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, ariza_adi, baslangic_zamani, durum FROM alarm_logs WHERE durum = 'Aktif' ORDER BY id DESC")
        active_rows = cursor.fetchall()
        for row in active_rows:
            try:
                start_dt = datetime.strptime(row["baslangic_zamani"], "%Y-%m-%d %H:%M:%S")
                diff_sec = max(0, int((now - start_dt).total_seconds()))
                dk = diff_sec // 60
                sn = diff_sec % 60
                sure_str = f"{dk:02d}:{sn:02d}"
            except Exception:
                sure_str = "00:00"

            active_alarms_list.append({
                "id": row["id"],
                "ariza_adi": row["ariza_adi"],
                "baslangic_zamani": row["baslangic_zamani"],
                "gecen_sure": sure_str,
                "durum": row["durum"]
            })
        conn.close()

    return {
        "telemetry": cur_telemetry,
        "active_alarms": active_alarms_list,
        "active_count": len(active_alarms_list),
        "history": history_list,
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S")
    }

@app.get("/api/alarms")
async def get_alarms():
    """Veritabanındaki tüm arıza kayıtlarını döner."""
    now = datetime.now()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, ariza_adi, baslangic_zamani, bitis_zamani, toplam_sure, durum 
        FROM alarm_logs 
        ORDER BY id DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    alarms = []
    for r in rows:
        gecen_sure = r["toplam_sure"]
        if r["durum"] == "Aktif":
            try:
                start_dt = datetime.strptime(r["baslangic_zamani"], "%Y-%m-%d %H:%M:%S")
                diff_sec = max(0, int((now - start_dt).total_seconds()))
                dk = diff_sec // 60
                sn = diff_sec % 60
                gecen_sure = f"{dk:02d}:{sn:02d}"
            except Exception:
                gecen_sure = "00:00"

        alarms.append({
            "id": r["id"],
            "ariza_adi": r["ariza_adi"],
            "baslangic_zamani": r["baslangic_zamani"],
            "bitis_zamani": r["bitis_zamani"] or "-",
            "toplam_sure": r["toplam_sure"] or "-",
            "gecen_sure": gecen_sure,
            "durum": r["durum"]
        })

    return alarms
