# debug_sd.py — Pinpoint why the SD card won't mount.
#
# Run this in Thonny / MicroPico, then paste the ENTIRE output back so we
# can see exactly which step fails.
#
# It does four things:
#   1. Reports MicroPython build info.
#   2. Manually talks to the card at 100 kHz, sends CMD0 (GO_IDLE_STATE),
#      and reports the raw response byte. This is the single most diagnostic
#      test — it tells us whether the wiring is right.
#   3. Tries the standard driver at 1 MHz (what main.py uses).
#   4. If mount succeeds, lists the root, writes a probe file, reads it
#      back, and deletes it.

from machine import Pin, SPI
import sys
import time

SPI_ID       = 0
SPI_SCK_PIN  = 2
SPI_MOSI_PIN = 3
SPI_MISO_PIN = 4
SPI_CS_PIN   = 5

print("=" * 60)
print("MicroPython:", sys.implementation)
try:
    import os
    print("uname:      ", os.uname())
except:
    pass
print("SPI pins:   SCK={} MOSI={} MISO={} CS={}".format(
    SPI_SCK_PIN, SPI_MOSI_PIN, SPI_MISO_PIN, SPI_CS_PIN))
print("=" * 60)


# ---------- Stage 1: raw CMD0 test ----------
print("\n[1] Raw SD init probe (100 kHz)")
print("    Sending >=74 dummy clocks with CS HIGH, then CMD0...")

cs = Pin(SPI_CS_PIN, Pin.OUT, value=1)
spi = SPI(SPI_ID,
          baudrate=100_000,
          polarity=0, phase=0,
          bits=8, firstbit=SPI.MSB,
          sck=Pin(SPI_SCK_PIN),
          mosi=Pin(SPI_MOSI_PIN),
          miso=Pin(SPI_MISO_PIN))

# 1a: At least 74 clocks with CS high for card wake-up
cs.value(1)
spi.write(b'\xff' * 16)

def send_cmd_raw(cmd, arg, crc):
    buf = bytearray(6)
    buf[0] = 0x40 | cmd
    buf[1] = (arg >> 24) & 0xFF
    buf[2] = (arg >> 16) & 0xFF
    buf[3] = (arg >>  8) & 0xFF
    buf[4] =  arg        & 0xFF
    buf[5] = crc
    spi.write(buf)
    # Read up to 8 bytes waiting for a response (first byte with bit7==0)
    for _ in range(8):
        r = spi.read(1, 0xFF)[0]
        if r != 0xFF:
            return r
    return 0xFF

# 1b: CMD0 (GO_IDLE_STATE). CRC 0x95 is the only one that matters for CMD0.
cs.value(0)
spi.read(1, 0xFF)  # dummy read
resp_cmd0 = send_cmd_raw(0, 0, 0x95)
cs.value(1)
spi.write(b'\xff')

print("    CMD0 response: 0x{:02X}".format(resp_cmd0))

if resp_cmd0 == 0x01:
    print("    -> 0x01 means the card entered idle state. WIRING IS GOOD.")
elif resp_cmd0 == 0xFF:
    print("    -> 0xFF means no response on MISO. Most likely causes:")
    print("       * MISO pin wrong (should be GP{}) or not connected".format(SPI_MISO_PIN))
    print("       * SD card not inserted / not powered / dead")
    print("       * Module expects 5V on VCC — try the other voltage pin")
elif resp_cmd0 == 0x00:
    print("    -> 0x00 is unusual. MISO may be stuck low (short to GND?).")
else:
    print("    -> Got 0x{:02X}. Card is responding but in an error state.".format(resp_cmd0))
    print("       Could be a different card class, or CS/MOSI wiring issue.")


# ---------- Stage 2: standard driver ----------
print("\n[2] Standard driver (sdcard.SDCard) at 1 MHz")
try:
    import sdcard
    spi.deinit()
    spi = SPI(SPI_ID,
              baudrate=1_000_000,
              polarity=0, phase=0,
              sck=Pin(SPI_SCK_PIN),
              mosi=Pin(SPI_MOSI_PIN),
              miso=Pin(SPI_MISO_PIN))
    # (already defined above with firstbit=SPI.MSB; second call uses defaults)
    sd = sdcard.SDCard(spi, Pin(SPI_CS_PIN))
    print("    sdcard.SDCard() init: OK")
    # Report card info if available
    try:
        n = sd.sectors
        print("    sectors: {}  (~{} MB)".format(n, (n * 512) // (1024 * 1024)))
    except Exception as e:
        print("    (couldn't read sector count: {})".format(e))
except Exception as e:
    print("    sdcard.SDCard() init FAILED: {}".format(e))
    print("    Stopping here. Check the CMD0 response above.")
    raise SystemExit


# ---------- Stage 3: mount + filesystem test ----------
print("\n[3] Mount + filesystem test")
import os
try:
    try:
        os.umount("/sd")
    except:
        pass
    os.mount(sd, "/sd")
    print("    mounted at /sd")
    print("    root contents:", os.listdir("/sd"))
    stats = os.statvfs("/sd")
    total_mb = (stats[0] * stats[2]) // (1024 * 1024)
    free_mb  = (stats[0] * stats[3]) // (1024 * 1024)
    print("    size: {} MB, free: {} MB".format(total_mb, free_mb))
    # Write / read probe
    with open("/sd/_probe.txt", "w") as f:
        f.write("ok")
    with open("/sd/_probe.txt", "r") as f:
        assert f.read() == "ok"
    os.remove("/sd/_probe.txt")
    print("    read/write probe: OK")
    print("\nSD CARD IS WORKING.")
except Exception as e:
    print("    mount/FS FAILED: {}".format(e))
    print("    The card init worked but the filesystem is unhappy.")
    print("    -> Re-format the card as FAT32 (not exFAT, not NTFS).")
    print("    -> 32 GB or smaller is safest.")
