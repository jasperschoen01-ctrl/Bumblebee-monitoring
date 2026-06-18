# simple.py — Minimal bumblebee audio logger.
#
# Records 60-second WAV files to /sd, named YYMMDD_HHMMSS.WAV
# (Amsterdam local time from the DS3231). No LED, no CYW43/WiFi,
# no recording window — just record continuously until reset.

from machine import I2S, Pin, SPI, I2C
import sdcard
import ds3231
import tz
import os
import struct
import time

# --- Pin config (must match your wiring) ---
I2S_SCK_PIN, I2S_WS_PIN, I2S_SD_PIN                   = 18, 19, 20
SPI_ID                                                = 0
SPI_SCK_PIN, SPI_MOSI_PIN, SPI_MISO_PIN, SPI_CS_PIN   = 2, 3, 4, 5
I2C_SDA_PIN, I2C_SCL_PIN                              = 0, 1

# --- Audio config ---
SAMPLE_RATE   = 11025
BITS          = 32
GAIN_SHIFT    = 14
CHUNK_SAMPLES = 512
FILE_SECONDS  = 60

# =============================================================
# SD card mount (with warm-up reads — see main.py for rationale)
# =============================================================
cs = Pin(SPI_CS_PIN, Pin.OUT, value=1)
time.sleep_ms(20)
spi = SPI(SPI_ID, baudrate=200_000, polarity=0, phase=0,
          sck=Pin(SPI_SCK_PIN),
          mosi=Pin(SPI_MOSI_PIN),
          miso=Pin(SPI_MISO_PIN))
spi.write(b'\xff' * 16)                      # >=74 dummy clocks, CS high

sd = sdcard.SDCard(spi, cs, baudrate=200_000)

buf = bytearray(512)
for _ in range(8):
    try:
        sd.readblocks(0, buf)
        sd.readblocks(0, buf)
        break
    except OSError:
        time.sleep_ms(100)

os.mount(sd, "/sd")
print("SD mounted")

# =============================================================
# RTC
# =============================================================
i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=100_000)
rtc = ds3231.DS3231(i2c)
print("RTC UTC =", rtc.datetime())

# =============================================================
# I2S mic
# =============================================================
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

raw_buf = bytearray(CHUNK_SAMPLES * 4)
out_buf = bytearray(CHUNK_SAMPLES * 2)
raw_mv  = memoryview(raw_buf)
out_mv  = memoryview(out_buf)

# Discard the 0.7 s power-on click
discard_needed = int(SAMPLE_RATE * 0.7)
discarded = 0
while discarded < discard_needed:
    n = audio.readinto(raw_mv)
    discarded += n // 4

# =============================================================
# 32-bit -> 16-bit (viper for speed)
# =============================================================
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

# =============================================================
# WAV helpers
# =============================================================
def write_header(f, sample_rate):
    # Placeholder sizes; patched on close.
    f.write(b'RIFF')
    f.write(struct.pack('<I', 0))
    f.write(b'WAVE')
    f.write(b'fmt ')
    f.write(struct.pack('<I', 16))
    f.write(struct.pack('<H', 1))              # PCM
    f.write(struct.pack('<H', 1))              # mono
    f.write(struct.pack('<I', sample_rate))
    f.write(struct.pack('<I', sample_rate * 2))
    f.write(struct.pack('<H', 2))
    f.write(struct.pack('<H', 16))
    f.write(b'data')
    f.write(struct.pack('<I', 0))

def patch_header(f, samples):
    data_size = samples * 2
    f.seek(4);  f.write(struct.pack('<I', 36 + data_size))
    f.seek(40); f.write(struct.pack('<I', data_size))

def make_filename():
    utc = rtc.datetime()
    y, mo, d, h, mi, s = tz.utc_to_amsterdam(*utc)
    return "/sd/{:02d}{:02d}{:02d}_{:02d}{:02d}{:02d}.WAV".format(
        y % 100, mo, d, h, mi, s)

# =============================================================
# Record loop
# =============================================================
target_samples = SAMPLE_RATE * FILE_SECONDS
print("Recording {} s per file ...".format(FILE_SECONDS))

while True:
    filename = make_filename()
    print("open", filename)
    samples = 0
    f = open(filename, "wb")
    try:
        write_header(f, SAMPLE_RATE)
        while samples < target_samples:
            n_bytes = audio.readinto(raw_mv)
            n_samp  = n_bytes // 4
            if n_samp:
                shift_32_to_16(raw_buf, out_buf, n_samp, GAIN_SHIFT)
                f.write(out_mv[:n_samp * 2])
                samples += n_samp
        patch_header(f, samples)
    finally:
        f.close()
    print("closed", filename)
