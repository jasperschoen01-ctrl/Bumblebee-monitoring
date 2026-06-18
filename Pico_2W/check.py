from machine import I2S, Pin, SPI, RTC
import network
import ntptime
import sdcard
import os
import struct
import time
import gc

# ================================================================
# --- Config --- same as main.py but shorter recording
# ================================================================
# Set to False to skip WiFi entirely and just stamp the RTC with MANUAL_TIME.
# Recommended for the check/test phase — it takes the CYW43 radio out of the
# picture so the SD mount + I2S record path is tested on its own, which is
# exactly the chain that was failing when WiFi was active.
USE_WIFI_SYNC    = True
MANUAL_TIME      = (2026, 4, 17, 0, 0, 0, 0, 0)  # (Y, M, D, weekday, h, m, s, subs)

WIFI_SSID        = "iPhone van Jasper"
WIFI_PASSWORD    = "KeesKoos1531"
RECORD_START_HR  = 0           # always record during check — ignore window
RECORD_STOP_HR   = 24
RECORD_SECONDS   = 10          # short recording for testing
DEVICE_NAME      = "pico2w"

# I2S mic
SCK_PIN          = 10   # BCLK Brown
WS_PIN           = 11   # LRCL White
SD_PIN_I2S       = 12   # DOUT Black

SAMPLE_RATE      = 11025
BITS             = 32
DISCARD_SAMPLES  = int(SAMPLE_RATE * 0.7)
CHUNK_SAMPLES    = 512

# SD card
SPI_SCK          = 18   # GP14 - wit
SPI_MOSI         = 19   # GP15 - bruin
SPI_MISO         = 16   # GP12 - grijs
SPI_CS           = 17   # GP13 - zwart

# ================================================================
# --- LED setup ---
# ================================================================
"""led = Pin("LED", Pin.OUT)   # Pico 2W onboard LED
"""
"""def blink(times, speed_ms=100):
    for _ in range(times):
        led.on()
        time.sleep_ms(speed_ms)
        led.off()
        time.sleep_ms(speed_ms)

def fast_blink_once():
    led.on()
    time.sleep_ms(50)
    led.off()
    time.sleep_ms(50)
"""
# ================================================================
# --- WiFi time sync ---
# ================================================================
def set_time_manual():
    """
    Skip WiFi and stamp the RTC directly. Use this when you just want to
    verify mount + I2S + record without the CYW43 radio interfering.
    """
    RTC().datetime(MANUAL_TIME)
    print("RTC set manually:", time.gmtime())
    blink(2, 200)   # 2 slow flashes = manual time set

def sync_time_via_wifi():
    print("Connecting to WiFi...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    # NOTE: wlan.scan() removed — it kept the radio busier than necessary
    # and correlated with SD-card mount failures on this hardware.

    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    for _ in range(15):
        if wlan.isconnected():
            break
        #fast_blink_once()   # fast blink while connecting
        time.sleep(1)

    if wlan.isconnected():
        print("WiFi connected:", wlan.ifconfig())
        ntptime.settime()
        print("Time synced, UTC:", time.gmtime())
        blink(3, 100)       # 3 quick flashes = WiFi + time OK
    else:
        print("WiFi FAILED — using fallback time")
        RTC().datetime((2026, 1, 1, 0, 0, 0, 0, 0))
        blink(5, 50)        # rapid 5 flashes = WiFi failed

    wlan.disconnect()
    wlan.active(False)
    print("WiFi off")
    # Let the 3V3 rail settle and defragment the heap after NTP allocations
    # before the SD driver tries to grab its own buffers.
    gc.collect()
    time.sleep_ms(500)

# ================================================================
# --- SD card mount ---
# ================================================================
def mount_sd():
    for attempt in range(3):
        try:
            spi = SPI(0,
                      baudrate=100_000,
                      polarity=0,
                      phase=0,
                      sck=Pin(SPI_SCK),
                      mosi=Pin(SPI_MOSI),
                      miso=Pin(SPI_MISO))
            sd = sdcard.SDCard(spi, Pin(SPI_CS))
            os.mount(sd, "/sd")
            blink(2, 100)
            print("SD mounted on attempt", attempt + 1)
            return
        except OSError as e:
            print("SD mount attempt", attempt + 1, "failed:", e)
            time.sleep(1)
    raise OSError("SD card mount failed after 3 attempts")

# ================================================================
# --- I2S setup ---
# ================================================================
def start_i2s():
    print("Starting I2S microphone...")
    audio = I2S(
        1,
        sck=Pin(SCK_PIN),
        ws=Pin(WS_PIN),
        sd=Pin(SD_PIN_I2S),
        mode=I2S.RX,
        bits=BITS,
        format=I2S.MONO,
        rate=SAMPLE_RATE,
        ibuf=32768
    )
    print("I2S started")
    return audio

# ================================================================
# --- WAV header ---
# ================================================================
def write_wav_header(f, sample_rate, num_samples):
    data_size = num_samples * 2
    f.write(b'RIFF')
    f.write(struct.pack('<I', 36 + data_size))
    f.write(b'WAVE')
    f.write(b'fmt ')
    f.write(struct.pack('<I', 16))
    f.write(struct.pack('<H', 1))
    f.write(struct.pack('<H', 1))
    f.write(struct.pack('<I', sample_rate))
    f.write(struct.pack('<I', sample_rate * 2))
    f.write(struct.pack('<H', 2))
    f.write(struct.pack('<H', 16))
    f.write(b'data')
    f.write(struct.pack('<I', data_size))

# ================================================================
# --- Helpers ---
# ================================================================
def make_filename():
    t = time.gmtime()
    fname = "/sd/{:04d}{:02d}{:02d}_{:02d}{:02d}{:02d}_{}.WAV".format(
        t[0], t[1], t[2],
        t[3], t[4], t[5],
        DEVICE_NAME
    )
    print("Filename:", fname)
    return fname

def log_error(filename, error):
    try:
        with open("/sd/errors.txt", "a") as log:
            log.write("{} error: {}\n".format(filename, str(error)))
        print("Error logged to errors.txt")
    except:
        print("Could not write to error log")

# ================================================================
# --- Setup ---
# ================================================================
print("=== CHECK.PY STARTING ===")
print("Free RAM before setup:", end=" ")
gc.collect()
print(gc.mem_free(), "bytes")

# Order: time first, then SD, then I2S.
# If USE_WIFI_SYNC is False we just stamp the RTC manually — this avoids the
# CYW43/SD interaction that was making mounts fail. If USE_WIFI_SYNC is True,
# sync_time_via_wifi() already does gc.collect() + a settle delay before
# returning, so the SPI bus and 3V3 rail are clean by the time mount_sd runs.
if USE_WIFI_SYNC:
    sync_time_via_wifi()
else:
    set_time_manual()

mount_sd()
audio_in = start_i2s()

print("Discarding startup noise...")
discard_buf = bytearray(DISCARD_SAMPLES * 4)
audio_in.readinto(discard_buf)
print("Mic stabilised")

chunk         = bytearray(CHUNK_SAMPLES * 4)
out           = bytearray(CHUNK_SAMPLES * 2)
total_samples = SAMPLE_RATE * RECORD_SECONDS

print("Free RAM after setup:", end=" ")
gc.collect()
print(gc.mem_free(), "bytes")
print("Starting", RECORD_SECONDS, "second test recording...")

# ================================================================
# --- Single test recording ---
# ================================================================
filename = make_filename()

try:
    f = open(filename, 'wb')
    write_wav_header(f, SAMPLE_RATE, total_samples)
    print("File opened, recording now...")

    samples_written = 0

    while samples_written < total_samples:

        remaining = total_samples - samples_written
        to_read   = min(CHUNK_SAMPLES, remaining)
        num_read  = audio_in.readinto(memoryview(chunk)[:to_read * 4])
        n_samples = num_read // 4

        batch = struct.unpack('<' + 'i' * n_samples, chunk[:num_read])
        for idx, s in enumerate(batch):
            s16 = s >> 14
            s16 = max(-32768, min(32767, s16))
            struct.pack_into('<h', out, idx * 2, s16)
        f.write(memoryview(out)[:n_samples * 2])

        samples_written += n_samples

        # Toggle LED every second — no sleep, just counter check
        if (samples_written % SAMPLE_RATE) < CHUNK_SAMPLES:
            led.toggle()
            print("Recorded:", samples_written // SAMPLE_RATE, "seconds")

    f.close()
    print("Recording complete, saved:", filename)
    blink(3, 200)   # 3 slow flashes = recording saved OK

    # Check file exists and size
    stat = os.stat(filename)
    print("File size:", stat[6], "bytes")
    expected = 44 + total_samples * 2
    if stat[6] == expected:
        print("File size CORRECT")
    else:
        print("File size WRONG — expected", expected, "got", stat[6])

    # List all files on SD card
    print("Files on SD card:", os.listdir("/sd"))

except Exception as e:
    print("RECORDING FAILED:", e)
    blink(5, 50)
    try:
        f.close()
    except:
        pass
    log_error(filename, e)

print("=== CHECK.PY COMPLETE ===")
audio_in.deinit()


"""State                Pattern
WiFi connecting         Fast blink — every 200ms
WiFi connected          3 quick flashes
SD card mounted         2 quick flashes
Recording               Slow pulse — 1 second on, 1 second off... actually this causes a problem — see below
Error                   Rapid flash 5 times
Outside recording window Single blink every 30 seconds"""