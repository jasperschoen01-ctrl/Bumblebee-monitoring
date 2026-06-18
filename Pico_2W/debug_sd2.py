# debug_sd2.py — Second SD debug pass.
#
# We already know the wiring is good and the card initialises. The problem
# is that mount() times out reading block 0. This script:
#
#   1. Initialises the card once.
#   2. Sweeps the SPI baud rate, manually reads block 0 at each rate, and
#      reports which succeed.
#   3. For the first rate that works, dumps the filesystem signature
#      (FAT12/16/32 vs exFAT vs nothing).
#   4. Tries to mount at that rate.
#
# Paste the FULL output back.

from machine import Pin, SPI
import sdcard
import os
import time

SPI_ID       = 0
SPI_SCK_PIN  = 2
SPI_MOSI_PIN = 3
SPI_MISO_PIN = 4
SPI_CS_PIN   = 5

BAUDS = [200_000, 400_000, 800_000, 1_320_000, 2_000_000]

def make_spi(baud):
    return SPI(SPI_ID,
               baudrate=baud, polarity=0, phase=0,
               sck=Pin(SPI_SCK_PIN),
               mosi=Pin(SPI_MOSI_PIN),
               miso=Pin(SPI_MISO_PIN))

print("=" * 60)
print("Sweeping SPI baud rates to find one the card is happy with.")
print("=" * 60)

# Make one SDCard instance at a safe baud, then poke the underlying SPI
# object between tests.
spi = make_spi(1_320_000)
cs  = Pin(SPI_CS_PIN, Pin.OUT, value=1)

try:
    sd = sdcard.SDCard(spi, cs)   # this bumps baud to 1.32 MHz after init
    print("Card OK: {} sectors (~{} MB)".format(
        sd.sectors, (sd.sectors * 512) // (1024 * 1024)))
except Exception as e:
    print("Init failed: {}".format(e))
    raise SystemExit

working_baud = None
block0 = None

for baud in BAUDS:
    print("\n--- {} Hz ---".format(baud))
    try:
        sd.spi.init(baudrate=baud, phase=0, polarity=0)
    except Exception as e:
        print("  can't re-init SPI at this rate: {}".format(e))
        continue

    buf = bytearray(512)
    t0  = time.ticks_ms()
    try:
        sd.readblocks(0, buf)
        dt = time.ticks_diff(time.ticks_ms(), t0)
        print("  readblocks(0) OK in {} ms".format(dt))
        # Try a few more blocks for reliability
        err = None
        for blk in (0, 1, 2, 100, 1000):
            try:
                sd.readblocks(blk, buf)
            except Exception as e:
                err = (blk, e)
                break
        if err is None:
            print("  spot-reads of blocks 0,1,2,100,1000 all OK")
            if working_baud is None:
                working_baud = baud
                block0 = bytes(buf) if blk == 0 else None
                # we want block 0 specifically
                sd.readblocks(0, buf)
                block0 = bytes(buf)
        else:
            print("  reading block {} failed: {}".format(err[0], err[1]))
    except Exception as e:
        dt = time.ticks_diff(time.ticks_ms(), t0)
        print("  readblocks(0) FAILED after {} ms: {}".format(dt, e))

print("\n" + "=" * 60)
if working_baud is None:
    print("No baud rate worked. This is very likely a power / decoupling issue.")
    print("  - Add a 10 uF (or larger) cap between SD VCC and GND near the module.")
    print("  - Use short wires (<10 cm) for all SD lines.")
    print("  - Try powering the SD module from VBUS (5V, pin 40) if the module")
    print("    has an onboard regulator; otherwise stick to 3V3 but shorten wires.")
    raise SystemExit

print("First working baud: {} Hz".format(working_baud))

# --- Filesystem inspection ---
print("\nBlock 0 first 16 bytes: ", " ".join("{:02X}".format(b) for b in block0[:16]))
print("Block 0 bytes 0x1FE-1FF: {:02X} {:02X}  (should be 55 AA)".format(
    block0[0x1FE], block0[0x1FF]))

# FAT signature is at offset 0x36 ("FAT12"/"FAT16") or 0x52 ("FAT32"/"FAT    ")
sig16 = bytes(block0[0x36:0x36+8])
sig32 = bytes(block0[0x52:0x52+8])
exfat = bytes(block0[0x03:0x03+5])  # "EXFAT"

print("\nFilesystem signature at 0x36: {}".format(sig16))
print("Filesystem signature at 0x52: {}".format(sig32))
print("Bytes 0x03-0x07 (for exFAT): {}".format(exfat))

if b"FAT32" in sig32 or b"FAT32" in sig16:
    print("-> Filesystem: FAT32 (supported)")
elif b"FAT16" in sig16 or b"FAT12" in sig16 or b"FAT" in sig16:
    print("-> Filesystem: FAT12/16 (supported)")
elif exfat.upper() == b"EXFAT":
    print("-> Filesystem: exFAT  (NOT SUPPORTED by MicroPython's uos)")
    print("   Re-format the card as FAT32 on your Mac:")
    print("     Disk Utility -> select the card -> Erase -> MS-DOS (FAT)")
else:
    print("-> Unknown/no filesystem. Re-format as FAT32.")

# --- Mount test ---
print("\nMounting at {} Hz ...".format(working_baud))
try:
    sd.spi.init(baudrate=working_baud, phase=0, polarity=0)
    try:
        os.umount("/sd")
    except:
        pass
    os.mount(sd, "/sd")
    print("MOUNT OK. Root:", os.listdir("/sd"))
    stats = os.statvfs("/sd")
    total_mb = (stats[0] * stats[2]) // (1024 * 1024)
    free_mb  = (stats[0] * stats[3]) // (1024 * 1024)
    print("size: {} MB, free: {} MB".format(total_mb, free_mb))
    print("\nSUCCESS. Set baudrate={} in main.py's mount_sd() SPI call.".format(working_baud))
except Exception as e:
    print("Mount still failed: {}".format(e))
