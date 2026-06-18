# added a failsafe if memory crashes
# + bulletproof filenames: boot counter + recording index + collision check
# + boot log so you can detect WDT resets in post-analysis
# + LED status blinks for field diagnostics
from machine import I2S, Pin, SPI, RTC, WDT
import network
import ntptime
import sdcard
import os
import struct
import time
import gc

# ================================================================
# --- Section 1: Config ---
# ================================================================
WIFI_SSID        = "iPhone van Jasper"
WIFI_PASSWORD    = "Keeskoos1531"
RECORD_START_HR  = 5           # UTC hour to start
RECORD_STOP_HR   = 17          # UTC hour to stop
RECORD_SECONDS   = 60          # length of each file in seconds
DEVICE_NAME      = "pico2w"

# --- Audio ---
SCK_PIN          = 18
WS_PIN           = 19
SD_PIN_I2S       = 20
SAMPLE_RATE      = 11025
BITS             = 32
DISCARD_SAMPLES  = int(SAMPLE_RATE * 0.7)
CHUNK_SAMPLES    = 512

# --- SD card ---
SPI_SCK          = 14
SPI_MOSI         = 15
SPI_MISO         = 12
SPI_CS           = 13

# Globals updated at runtime
WIFI_SUCCESS     = False
BOOT_COUNT       = 0
RECORDING_INDEX  = 0

# ================================================================
# --- LED setup ---
# ================================================================
led = Pin("LED", Pin.OUT)

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
# --- Section 2: WiFi time sync ---
# ================================================================
def sync_time_via_wifi():
    global WIFI_SUCCESS
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(15):
        if wlan.isconnected():
            break
        fast_blink_once()  # fast blink while connecting
        time.sleep(1)
    if wlan.isconnected():
        try:
            ntptime.settime()
            WIFI_SUCCESS = True
            blink(3, 100)  # 3 quick flashes = WiFi + time OK
        except:
            WIFI_SUCCESS = False
            RTC().datetime((2026, 1, 1, 0, 0, 0, 0, 0))
            blink(5, 50)   # rapid 5 = WiFi connected but NTP failed
    else:
        WIFI_SUCCESS = False
        RTC().datetime((2026, 1, 1, 0, 0, 0, 0, 0))
        blink(5, 50)       # rapid 5 = WiFi failed
    wlan.disconnect()
    wlan.active(False)

# ================================================================
# --- Section 3: SD card mount ---
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
            blink(2, 100)  # 2 quick flashes = SD mounted
            return
        except OSError as e:
            time.sleep(1)
    # If we got here, all 3 attempts failed
    blink(10, 50)  # 10 rapid flashes = SD failed (very bad)
    raise OSError("SD card mount failed after 3 attempts")

# ================================================================
# --- Section 4: I2S setup ---
# ================================================================
def start_i2s():
    return I2S(
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
# --- Boot counter & logging ---
# ================================================================
def get_boot_count():
    """Read boot counter from SD, increment, write back. Survives resets."""
    try:
        with open("/sd/boot_count.txt", "r") as f:
            count = int(f.read().strip())
    except:
        count = 0
    count += 1
    try:
        with open("/sd/boot_count.txt", "w") as f:
            f.write(str(count))
    except:
        pass
    return count

def log_boot():
    """Append a line to the boot log so WDT resets are visible later."""
    try:
        t = time.gmtime()
        with open("/sd/boot_log.txt", "a") as f:
            f.write("Boot {:04d}: {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} UTC (wifi={})\n".format(
                BOOT_COUNT,
                t[0], t[1], t[2],
                t[3], t[4], t[5],
                "ok" if WIFI_SUCCESS else "fail"
            ))
    except:
        pass

# ================================================================
# --- Helpers ---
# ================================================================
def make_filename():
    """
    Bulletproof filename:
    - timestamp:   when (or what the RTC thinks 'when' is)
    - b{boot}:     unique per power-on / WDT reset (survives clock resets)
    - n{index}:    unique per recording within this boot (survives clock skew)
    - device:     identifies which Pico produced it
    Final collision check appends _01, _02... if file somehow already exists.
    """
    global RECORDING_INDEX
    RECORDING_INDEX += 1
    t = time.gmtime()
    base = "/sd/{:04d}{:02d}{:02d}_{:02d}{:02d}{:02d}_b{:04d}_n{:05d}_{}".format(
        t[0], t[1], t[2],
        t[3], t[4], t[5],
        BOOT_COUNT,
        RECORDING_INDEX,
        DEVICE_NAME
    )
    fname = base + ".WAV"
    counter = 1
    while True:
        try:
            os.stat(fname)
            fname = "{}_{:02d}.WAV".format(base, counter)
            counter += 1
        except OSError:
            return fname

def in_recording_window():
    return RECORD_START_HR <= time.gmtime()[3] < RECORD_STOP_HR

def log_error(filename, error):
    try:
        with open("/sd/errors.txt", "a") as log:
            log.write("{} error: {}\n".format(filename, str(error)))
    except:
        pass

# ================================================================
# --- Setup ---
# ================================================================
# Single long blink at startup so you know the script is running
led.on()
time.sleep_ms(500)
led.off()
time.sleep_ms(500)

mount_sd()
sync_time_via_wifi()

BOOT_COUNT = get_boot_count()
log_boot()

audio_in = start_i2s()

# Discard startup noise once
discard_buf = bytearray(DISCARD_SAMPLES * 4)
audio_in.readinto(discard_buf)

# Layer 1: pre-allocate all buffers once before loop
chunk         = bytearray(CHUNK_SAMPLES * 4)
out           = bytearray(CHUNK_SAMPLES * 2)
total_samples = SAMPLE_RATE * RECORD_SECONDS

gc.collect()

# Layer 3: start watchdog with 8 second timeout
wdt = WDT(timeout=8000)

# ================================================================
# --- Section 6: Main loop ---
# ================================================================
loop_counter = 0

while True:

    wdt.feed()

    if not in_recording_window():
        # Single brief blink every ~30 seconds while idle
        # so you know the Pico is alive but not recording
        fast_blink_once()
        time.sleep(30)
        continue

    filename = make_filename()

    try:
        f = open(filename, 'wb')
        write_wav_header(f, SAMPLE_RATE, total_samples)

        # LED on solid = recording in progress
        led.on()

        samples_written = 0

        while samples_written < total_samples:

            wdt.feed()

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

        f.close()

        # LED off + 2 quick flashes = recording saved successfully
        led.off()
        blink(2, 80)

        loop_counter += 1
        if loop_counter % 10 == 0:
            gc.collect()

    except Exception as e:
        try:
            f.close()
        except:
            pass
        led.off()
        log_error(filename, e)
        # 5 rapid flashes = recording error
        blink(5, 50)
        wdt.feed()
        gc.collect()
        continue