from machine import I2S, Pin, SPI, RTC
import network
import ntptime
import sdcard
import os
import struct
import time
import gc

# ================================================================
# --- Config --- short test-recording variant of main3.py
# ================================================================
WIFI_SSID        = "iPhone van Jasper"
WIFI_PASSWORD    = "Keeskoos1531"
RECORD_START_HR  = 0           # always record during check — ignore window
RECORD_STOP_HR   = 24
RECORD_SECONDS   = 10          # short recording for testing
DEVICE_NAME      = "pico2w"

# I2S mic
SCK_PIN          = 18   # GP18
WS_PIN           = 19   # GP19
SD_PIN_I2S       = 20   # GP20

SAMPLE_RATE      = 11025
BITS             = 32
DISCARD_SAMPLES  = int(SAMPLE_RATE * 0.7)
CHUNK_SAMPLES    = 512

# SD card
SPI_SCK          = 14   # GP14 - wit
SPI_MOSI         = 15   # GP15 - bruin
SPI_MISO         = 12   # GP12 - grijs
SPI_CS           = 13   # GP13 - zwart

# Fallback time — only used if the RTC has nothing sensible AND WiFi fails.
# The RTC on the RP2350 keeps running across soft resets / WDT resets as long
# as the board stays powered, so we try to preserve a previously-synced time
# rather than clobbering it back to 2026-01-01 every time WiFi sync fails.
FALLBACK_TIME    = (2026, 1, 1, 0, 0, 0, 0, 0)
FALLBACK_YEAR    = 2026        # anything >= this is considered "already set"

# Runtime state
WIFI_SUCCESS     = False
TIME_SOURCE      = "unknown"   # "ntp", "rtc_preserved", or "fallback"

# ================================================================
# --- LED setup ---
# ================================================================
led = Pin("LED", Pin.OUT)   # Pico 2W onboard LED

def blink(times, speed_ms=100):
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

# ================================================================
# --- RTC sanity check ---
# ================================================================
def rtc_already_valid():
    """
    Return True if the RTC currently holds something that looks like a real,
    previously-synced timestamp rather than the power-on default or the
    fallback stamp. We use year >= FALLBACK_YEAR + 1 as the guard, so the
    fallback year itself (2026) is NOT considered 'valid' — only a year that
    could only come from a real NTP sync.
    """
    try:
        y = time.gmtime()[0]
        return y > FALLBACK_YEAR
    except Exception:
        return False

# ================================================================
# --- WiFi time sync (with no-clobber fallback) ---
# ================================================================
def sync_time_via_wifi():
    """
    Try to sync via NTP. On success, set WIFI_SUCCESS=True and TIME_SOURCE='ntp'.
    On failure, only overwrite the RTC with FALLBACK_TIME if the RTC does NOT
    already hold a previously-synced real time — this prevents wiping out good
    time info during field-test reboots where WiFi just happens to be down.
    """
    global WIFI_SUCCESS, TIME_SOURCE

    print("Connecting to WiFi...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    for _ in range(15):
        if wlan.isconnected():
            break
        fast_blink_once()
        time.sleep(1)

    if wlan.isconnected():
        print("WiFi connected:", wlan.ifconfig())
        try:
            ntptime.settime()
            WIFI_SUCCESS = True
            TIME_SOURCE  = "ntp"
            print("Time synced via NTP, UTC:", time.gmtime())
            blink(3, 100)       # 3 quick flashes = WiFi + time OK
        except Exception as e:
            print("NTP sync failed:", e)
            WIFI_SUCCESS = False
            if rtc_already_valid():
                TIME_SOURCE = "rtc_preserved"
                print("Keeping existing RTC time:", time.gmtime())
                blink(4, 80)    # 4 flashes = WiFi OK, NTP failed, RTC kept
            else:
                RTC().datetime(FALLBACK_TIME)
                TIME_SOURCE = "fallback"
                print("Using fallback time:", FALLBACK_TIME)
                blink(5, 50)    # 5 rapid flashes = had to use fallback
    else:
        print("WiFi FAILED")
        WIFI_SUCCESS = False
        if rtc_already_valid():
            TIME_SOURCE = "rtc_preserved"
            print("Keeping existing RTC time:", time.gmtime())
            blink(4, 80)
        else:
            RTC().datetime(FALLBACK_TIME)
            TIME_SOURCE = "fallback"
            print("Using fallback time:", FALLBACK_TIME)
            blink(5, 50)

    wlan.disconnect()
    wlan.active(False)
    print("WiFi off; time source =", TIME_SOURCE)

# ================================================================
# --- SD card mount ---
# ================================================================
def mount_sd():
    for attempt in range(3):
        try:
            spi = SPI(1,
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
    """
    Filename also encodes TIME_SOURCE so you can tell later which recordings
    had real NTP time versus preserved-RTC versus fallback-stamped time.
    """
    t = time.gmtime()
    fname = "/sd/{:04d}{:02d}{:02d}_{:02d}{:02d}{:02d}_{}_{}.WAV".format(
        t[0], t[1], t[2],
        t[3], t[4], t[5],
        TIME_SOURCE,
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
print("=== CHECK3.PY STARTING ===")
print("Free RAM before setup:", end=" ")
gc.collect()
print(gc.mem_free(), "bytes")

# Order used here: sync -> (gc) -> mount -> record.
# We sync first so the WiFi radio is done before SPI traffic to the SD card.
# gc.collect() between the two steps defragments the heap that ntptime leaves
# behind, which makes the SD driver's buffer allocation more reliable.
sync_time_via_wifi()
gc.collect()
time.sleep_ms(500)     # small settle time for the 3V3 rail after radio off
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
print("Time source:", TIME_SOURCE)
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

print("=== CHECK3.PY COMPLETE ===")
audio_in.deinit()


"""State                       Pattern
WiFi connecting                 Fast blink every 200 ms
WiFi + NTP OK                   3 quick flashes
WiFi OK, NTP failed, RTC kept   4 medium flashes
WiFi failed, RTC kept           4 medium flashes
Had to use fallback time        5 rapid flashes
SD card mounted                 2 quick flashes
Recording saved OK              3 slow flashes
Recording error                 5 rapid flashes
"""
