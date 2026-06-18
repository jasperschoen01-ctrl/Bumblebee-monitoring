# main.py — Bumblebee audio logger for Raspberry Pi Pico 2 W.
#
# One WAV file per minute, named YYMMDD_HHMM00_Pico.WAV (Amsterdam local time).
# Recording runs 07:00 -> 19:00 Amsterdam time. Outside that window, the
# microphone keeps running but samples are discarded, so the 0.7 s start-up
# click only happens once at power-on and never between files.
#
# Hardware / pin map (Pico / Pico 2 W):
#   SPH0645 (I2S1)  : BCLK=GP18, LRCL=GP19, DOUT=GP20, SEL=GND, 3V3, GND
#   SD card (SPI0)  : SCK=GP2,  MOSI=GP3,  MISO=GP4,  CS=GP5, 3V3, GND
#   DS3231  (I2C0)  : SDA=GP0,  SCL=GP1,   3V3, GND  (CR2032 keeps it ticking)
#   Status LED      : onboard "LED"
#
# =====================================================================
# LED codes — use these to tell at a glance whether the device is alive
# =====================================================================
#   Power-on              : 1 long flash (1 s)
#   SD mounted OK         : 2 short flashes
#   DS3231 reading OK     : 3 short flashes
#   I2S started           : 4 short flashes
#   Logger ready          : 5 short flashes
#   Inside window, file OK: very brief flash every time a file closes
#   Outside window (idle) : slow triple-blink every 10 s ("heartbeat")
#   Fatal error           : continuous rapid flashing forever
#
# See /sd/errors.txt on the card for any write-time errors.
# See /sd/boot.txt for a log of each power-on.

from machine import I2S, Pin, SPI, I2C, WDT
import sdcard
import ds3231
import tz
import os
import struct
import time
import gc

# ==============================================================
# --- Config ---
# ==============================================================
DEVICE_NAME        = "Pico"
SAMPLE_RATE        = 11025
BITS               = 32               # I2S word width (SPH0645)
GAIN_SHIFT         = 14               # right-shift to get 16-bit sample
DISCARD_SECONDS    = 0.7              # initial click discard after power-on
CHUNK_SAMPLES      = 512              # samples per I2S read

# Recording window (Amsterdam local time, 24 h). Records when start <= h < stop.
RECORD_START_HOUR  = 7
RECORD_STOP_HOUR   = 19

# --- Pin assignments ---
I2S_SCK_PIN = 18
I2S_WS_PIN  = 19
I2S_SD_PIN  = 20

SPI_ID       = 0
SPI_SCK_PIN  = 2
SPI_MOSI_PIN = 3
SPI_MISO_PIN = 4
SPI_CS_PIN   = 5

I2C_SDA_PIN = 0
I2C_SCL_PIN = 1

# ==============================================================
# --- LED (lazy) ---
# ==============================================================
# IMPORTANT: on the Pico 2 W the onboard LED is driven through the CYW43
# WiFi chip. The moment you evaluate Pin("LED", Pin.OUT), MicroPython loads
# CYW43 firmware and draws a significant current spike on 3V3 for ~1 s. If
# the SD card shares that 3V3 rail it can brown out and the mount times
# out. We therefore DO NOT instantiate the LED until AFTER the SD card has
# mounted successfully.
led = None

def _led_init():
    global led
    if led is None:
        led = Pin("LED", Pin.OUT)

def blink(times, on_ms=80, off_ms=80):
    if led is None:
        # Early-boot: just print. We'll repeat the blink audibly-ish once
        # the LED is available below.
        print("[blink x{}]".format(times))
        return
    for _ in range(times):
        led.on(); time.sleep_ms(on_ms)
        led.off(); time.sleep_ms(off_ms)

def long_flash(ms=1000):
    if led is None:
        print("[long flash]")
        return
    led.on(); time.sleep_ms(ms); led.off(); time.sleep_ms(200)

def fatal(msg):
    # Try the SD log, but don't depend on it.
    print("FATAL:", msg)
    try:
        with open("/sd/boot.txt", "a") as f:
            f.write("FATAL: {}\n".format(msg))
    except:
        pass
    # If we have an LED, flash forever. If not, just sit.
    if led is not None:
        while True:
            blink(1, 50, 50)
    else:
        while True:
            time.sleep(1)

# ==============================================================
# --- SD card ---
# ==============================================================
# Cold-start init of an SD card over SPI is flaky. Observed behaviour on
# this board:
#   - debug_sd.py Stage 2 (sdcard.SDCard init at 1 MHz)  : OK
#   - debug_sd.py Stage 3 (os.mount immediately after)   : "timeout waiting for response"
#   - debug_sd2.py (did readblocks(0) at several rates
#     before os.mount at 200 kHz)                        : OK
#
# So the very first readblocks(0) after init is unreliable on this card.
# os.mount fails because *it* is that first readblocks. The fix is to do
# a handful of warm-up readblocks ourselves first, with retries and a small
# delay between attempts, and THEN call os.mount. We also tie CS HIGH before
# creating SPI so the card never sees clocks with CS floating.
_SD_MOUNT_PLAN = (
    (  200_000, 500),
    (  200_000, 1000),
    (  100_000, 1500),
    (  100_000, 2000),
    (  400_000, 2000),
)

def _warm_up_read(sd, attempts=8, delay_ms=80):
    """Retry readblocks(0) until it works, or give up."""
    buf = bytearray(512)
    last = None
    for i in range(attempts):
        try:
            sd.readblocks(0, buf)
            # One more read to be sure the card is awake and reliable.
            sd.readblocks(0, buf)
            return True
        except Exception as e:
            last = e
            time.sleep_ms(delay_ms)
    print("  warm-up readblocks gave up: {}".format(last))
    return False

def mount_sd():
    # Pin CS HIGH *before* we wiggle SCK/MOSI, so the card never interprets
    # a floating CS as "selected" during SPI bring-up. Reuse this same pin
    # object across all retry attempts.
    cs_pin = Pin(SPI_CS_PIN, Pin.OUT, value=1)
    time.sleep_ms(20)

    last_err = None
    for baud, delay_ms in _SD_MOUNT_PLAN:
        spi = None
        try:
            spi = SPI(SPI_ID,
                      baudrate=baud,
                      polarity=0, phase=0,
                      sck=Pin(SPI_SCK_PIN),
                      mosi=Pin(SPI_MOSI_PIN),
                      miso=Pin(SPI_MISO_PIN))
            # >=74 dummy clocks with CS high for SD wake-up.
            cs_pin.value(1)
            spi.write(b'\xff' * 16)

            # Pass explicit baudrate so the driver doesn't silently bump.
            sd = sdcard.SDCard(spi, cs_pin, baudrate=baud)

            # Warm-up readblocks BEFORE os.mount, with retries. This is the
            # critical step that distinguishes this path from the one that
            # fails in debug_sd.py Stage 3.
            if not _warm_up_read(sd):
                raise OSError("warm-up readblocks failed at {} Hz".format(baud))

            os.mount(sd, "/sd")
            print("SD mounted at {} Hz".format(baud))
            blink(2)
            return
        except Exception as e:
            last_err = e
            print("SD mount at {} Hz failed: {}".format(baud, e))
            try: os.umount("/sd")
            except: pass
            try:
                if spi is not None:
                    spi.deinit()
            except: pass
            time.sleep_ms(delay_ms)
    fatal("SD mount failed after all retries: {}".format(last_err))

# ==============================================================
# --- DS3231 ---
# ==============================================================
def init_rtc():
    i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=100_000)
    try:
        rtc = ds3231.DS3231(i2c)
        # Quick sanity-read. If the RTC is unset it often returns 2000-01-01.
        dt = rtc.datetime()
        print("DS3231 UTC =", dt)
        blink(3)
        return rtc
    except OSError as e:
        fatal("DS3231 not responding: {}".format(e))

# ==============================================================
# --- I2S ---
# ==============================================================
def start_i2s():
    audio = I2S(
        1,
        sck=Pin(I2S_SCK_PIN),
        ws=Pin(I2S_WS_PIN),
        sd=Pin(I2S_SD_PIN),
        mode=I2S.RX,
        bits=BITS,
        format=I2S.MONO,
        rate=SAMPLE_RATE,
        ibuf=32768,
    )
    blink(4)
    return audio

# ==============================================================
# --- 32-bit -> 16-bit (viper) ---
# ==============================================================
@micropython.viper
def shift_32_to_16(src: ptr32, dst: ptr16, n: int, shift: int):
    i = 0
    while i < n:
        v = src[i] >> shift
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        dst[i] = v
        i += 1

# ==============================================================
# --- WAV header ---
# ==============================================================
def write_wav_header(f, num_samples, sample_rate):
    data_size   = num_samples * 2
    byte_rate   = sample_rate * 2
    block_align = 2
    f.write(b'RIFF')
    f.write(struct.pack('<I', 36 + data_size))
    f.write(b'WAVE')
    f.write(b'fmt ')
    f.write(struct.pack('<I', 16))
    f.write(struct.pack('<H', 1))        # PCM
    f.write(struct.pack('<H', 1))        # mono
    f.write(struct.pack('<I', sample_rate))
    f.write(struct.pack('<I', byte_rate))
    f.write(struct.pack('<H', block_align))
    f.write(struct.pack('<H', 16))
    f.write(b'data')
    f.write(struct.pack('<I', data_size))

def patch_wav_header(f, samples_written):
    data_size = samples_written * 2
    f.seek(4)
    f.write(struct.pack('<I', 36 + data_size))
    f.seek(40)
    f.write(struct.pack('<I', data_size))

# ==============================================================
# --- Filename (from Amsterdam local time tuple) ---
# ==============================================================
def make_filename(local_t):
    y, mo, d, h, mi, _ = local_t
    return "/sd/{:02d}{:02d}{:02d}_{:02d}{:02d}00_{}.WAV".format(
        y % 100, mo, d, h, mi, DEVICE_NAME
    )

# ==============================================================
# --- Error / boot log ---
# ==============================================================
def log_line(path, msg):
    try:
        with open(path, "a") as f:
            # timestamp using whatever RTC we have
            try:
                utc = rtc.datetime()
                local = tz.utc_to_amsterdam(*utc)
                f.write("{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} AMS  {}\n".format(
                    local[0], local[1], local[2], local[3], local[4], local[5], msg))
            except:
                f.write("(no rtc)  {}\n".format(msg))
    except:
        pass

# ==============================================================
# --- Startup ---
# ==============================================================
print("\n=== Bumblebee logger booting ===")

# Let the 3V3 rail settle after power-on before we hit the SD card.
time.sleep_ms(500)

# IMPORTANT: mount the SD card BEFORE touching the LED. On the Pico 2 W the
# LED lives on the CYW43 WiFi chip and Pin("LED") triggers firmware load,
# which pulls ~100 mA of transient current on 3V3 and can brown out the SD.
mount_sd()

# Now it's safe to wake the LED/CYW43.
_led_init()
long_flash(1000)       # "power applied" (late, but visible)

rtc      = init_rtc()
audio_in = start_i2s()

# Preallocate buffers once
raw_buf = bytearray(CHUNK_SAMPLES * 4)
out_buf = bytearray(CHUNK_SAMPLES * 2)
raw_mv  = memoryview(raw_buf)
out_mv  = memoryview(out_buf)

# Discard the 0.7 s start-up click, once
discard_needed = int(SAMPLE_RATE * DISCARD_SECONDS)
discarded = 0
while discarded < discard_needed:
    n = audio_in.readinto(raw_mv)
    discarded += n // 4

# Ready!
blink(5, 60, 60)
log_line("/sd/boot.txt", "boot OK")
print("Logger ready. Window: {:02d}:00 - {:02d}:00 Amsterdam".format(
    RECORD_START_HOUR, RECORD_STOP_HOUR))

gc.collect()
wdt = WDT(timeout=8000)

# ==============================================================
# --- Main loop ---
# ==============================================================
# State: f (current open file), target_minute, samples_written.
# Transitions:
#   outside -> inside : open file for current minute
#   minute ticks over : close file, open next
#   inside -> outside : close file
f                 = None
target_minute     = None
samples_written   = 0
inside_window     = False
last_heartbeat_mi = -1

while True:
    wdt.feed()

    # Always pull from I2S to keep the mic alive and the buffer drained.
    n_bytes = audio_in.readinto(raw_mv)
    n_samp  = n_bytes // 4

    # Read the clock
    utc   = rtc.datetime()
    local = tz.utc_to_amsterdam(*utc)
    hour  = local[3]
    minute= local[4]

    in_window = (RECORD_START_HOUR <= hour < RECORD_STOP_HOUR)

    if in_window:
        # Entering the window or first file after boot
        if not inside_window:
            try:
                filename      = make_filename(local)
                f             = open(filename, "wb")
                write_wav_header(f, 0, SAMPLE_RATE)
                target_minute = minute
                samples_written = 0
                inside_window = True
                print("[{}:{:02d}] open {}".format(hour, minute, filename))
            except Exception as e:
                log_line("/sd/errors.txt", "open1: {}".format(e))
                time.sleep_ms(200)
                continue

        # Write this chunk
        if n_samp > 0:
            try:
                shift_32_to_16(raw_buf, out_buf, n_samp, GAIN_SHIFT)
                f.write(out_mv[:n_samp * 2])
                samples_written += n_samp
            except Exception as e:
                log_line("/sd/errors.txt", "write: {}".format(e))

        # Minute ticked over -> close current, open next
        if minute != target_minute:
            try:
                patch_wav_header(f, samples_written)
                f.close()
                blink(1, 30, 0)
            except Exception as e:
                log_line("/sd/errors.txt", "close: {}".format(e))
            try:
                filename      = make_filename(local)
                f             = open(filename, "wb")
                write_wav_header(f, 0, SAMPLE_RATE)
                target_minute = minute
                samples_written = 0
                print("[{:02d}:{:02d}] open {}".format(hour, minute, filename))
            except Exception as e:
                log_line("/sd/errors.txt", "open: {}".format(e))
                f = None
                inside_window = False
                time.sleep_ms(500)

    else:
        # Outside window: close any open file, idle with a heartbeat blink.
        if inside_window and f is not None:
            try:
                patch_wav_header(f, samples_written)
                f.close()
            except:
                pass
            f = None
            inside_window = False
            print("[{:02d}:{:02d}] window closed, idling".format(hour, minute))

        # Heartbeat every 10 s while idle
        s = local[5]
        if (s // 10) != last_heartbeat_mi:
            last_heartbeat_mi = s // 10
            blink(3, 40, 60)
