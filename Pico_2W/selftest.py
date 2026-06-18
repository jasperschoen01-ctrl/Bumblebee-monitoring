# selftest.py — Manual end-to-end diagnostic.
#
# Run this from Thonny / MicroPico BEFORE deploying. It exercises every
# subsystem and prints a clear pass/fail line for each one.
#
# What it does:
#   1. Mounts the SD card, writes and reads a small probe file.
#   2. Reads the DS3231 and prints both UTC and Amsterdam local time.
#   3. Starts I2S, records a 3-second WAV to /sd/selftest.WAV.
#   4. Measures RMS amplitude so you can tell if the mic is actually picking
#      up sound (tap the mic while this runs).
#
# After it finishes:
#   - Unplug the Pico
#   - Put the SD card in your Mac
#   - Open /Volumes/<card>/selftest.WAV in QuickTime or Audacity

from machine import I2S, Pin, SPI, I2C
import sdcard
import ds3231
import tz
import os
import struct
import time

# --- Pin config (must match main.py) ---
I2S_SCK_PIN, I2S_WS_PIN, I2S_SD_PIN          = 18, 19, 20
SPI_ID                                       = 0
SPI_SCK_PIN, SPI_MOSI_PIN, SPI_MISO_PIN, SPI_CS_PIN = 2, 3, 4, 5
I2C_SDA_PIN, I2C_SCL_PIN                     = 0, 1

SAMPLE_RATE   = 11025
TEST_SECONDS  = 3
GAIN_SHIFT    = 14

led = Pin("LED", Pin.OUT)


def ok(msg):   print(" [ OK ] ", msg)
def fail(msg): print(" [FAIL] ", msg)


print("\n===== Bumblebee Logger Self-Test =====\n")

# 1) SD card
try:
    spi = SPI(SPI_ID, baudrate=1_000_000, polarity=0, phase=0,
              sck=Pin(SPI_SCK_PIN), mosi=Pin(SPI_MOSI_PIN), miso=Pin(SPI_MISO_PIN))
    sd = sdcard.SDCard(spi, Pin(SPI_CS_PIN))
    os.mount(sd, "/sd")
    ok("SD card mounted at /sd")
    # Write/read probe
    with open("/sd/selftest_probe.txt", "w") as f:
        f.write("hello from pico\n")
    with open("/sd/selftest_probe.txt", "r") as f:
        assert f.read().strip() == "hello from pico"
    os.remove("/sd/selftest_probe.txt")
    ok("SD read/write OK")
    # Show free space
    s = os.statvfs("/sd")
    total_mb = (s[0] * s[2]) // (1024 * 1024)
    free_mb  = (s[0] * s[3]) // (1024 * 1024)
    ok("SD size: {} MB, free: {} MB".format(total_mb, free_mb))
except Exception as e:
    fail("SD test: {}".format(e))
    raise SystemExit

# 2) DS3231
try:
    i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=100_000)
    devices = i2c.scan()
    if 0x68 not in devices:
        fail("DS3231 not on I2C bus (found {})".format([hex(a) for a in devices]))
        raise SystemExit
    rtc = ds3231.DS3231(i2c)
    utc = rtc.datetime()
    local = tz.utc_to_amsterdam(*utc)
    ok("DS3231 UTC:        {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*utc))
    ok("DS3231 Amsterdam:  {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*local))
    ok("DS3231 temp:       {:.1f} C".format(rtc.temperature()))
    if utc[0] < 2025:
        fail("RTC appears unset (year = {}). Run set_rtc.py first.".format(utc[0]))
except Exception as e:
    fail("RTC test: {}".format(e))
    raise SystemExit

# 3) I2S + WAV
try:
    audio_in = I2S(1,
                   sck=Pin(I2S_SCK_PIN),
                   ws=Pin(I2S_WS_PIN),
                   sd=Pin(I2S_SD_PIN),
                   mode=I2S.RX, bits=32, format=I2S.MONO,
                   rate=SAMPLE_RATE, ibuf=32768)
    ok("I2S started at {} Hz".format(SAMPLE_RATE))

    CHUNK = 512
    raw = bytearray(CHUNK * 4)
    out = bytearray(CHUNK * 2)

    # Discard 0.7 s of click
    discard = int(SAMPLE_RATE * 0.7)
    d = 0
    while d < discard:
        n = audio_in.readinto(raw)
        d += n // 4

    target_samples = SAMPLE_RATE * TEST_SECONDS
    filename = "/sd/selftest.WAV"
    print("    Recording {} s to {} ... (tap the mic!)".format(TEST_SECONDS, filename))

    rms_accum   = 0
    rms_count   = 0
    samples_done = 0

    with open(filename, "wb") as f:
        # WAV header placeholder
        data_size = target_samples * 2
        f.write(b'RIFF'); f.write(struct.pack('<I', 36 + data_size)); f.write(b'WAVE')
        f.write(b'fmt '); f.write(struct.pack('<I', 16))
        f.write(struct.pack('<H', 1)); f.write(struct.pack('<H', 1))
        f.write(struct.pack('<I', SAMPLE_RATE))
        f.write(struct.pack('<I', SAMPLE_RATE * 2))
        f.write(struct.pack('<H', 2)); f.write(struct.pack('<H', 16))
        f.write(b'data'); f.write(struct.pack('<I', data_size))

        # Led on while recording
        led.on()

        while samples_done < target_samples:
            remaining = target_samples - samples_done
            to_read   = min(CHUNK, remaining)
            n_bytes   = audio_in.readinto(memoryview(raw)[:to_read * 4])
            n_samp    = n_bytes // 4

            # 32 -> 16, compute RMS
            batch = struct.unpack('<' + 'i' * n_samp, raw[:n_bytes])
            for idx, s in enumerate(batch):
                s16 = s >> GAIN_SHIFT
                if s16 > 32767: s16 = 32767
                if s16 < -32768: s16 = -32768
                struct.pack_into('<h', out, idx * 2, s16)
                rms_accum += s16 * s16
                rms_count += 1
            f.write(memoryview(out)[:n_samp * 2])
            samples_done += n_samp

        led.off()

    audio_in.deinit()

    import math
    rms = math.sqrt(rms_accum / rms_count) if rms_count else 0
    ok("Recorded {} samples ({} s)".format(samples_done, samples_done / SAMPLE_RATE))
    ok("RMS amplitude: {:.0f}  (silent room ~50-500, talking ~2000-8000)".format(rms))
    if rms < 5:
        fail("RMS is ~0 -- mic probably not wired. Check SEL, DOUT, 3V3, GND.")

except Exception as e:
    fail("I2S/recording test: {}".format(e))
    raise SystemExit

# Blink "all good" 10x fast
for _ in range(10):
    led.on(); time.sleep_ms(50); led.off(); time.sleep_ms(50)

print("\n===== Self-test complete =====")
print("Listen to /sd/selftest.WAV on your Mac to verify audio quality.")
