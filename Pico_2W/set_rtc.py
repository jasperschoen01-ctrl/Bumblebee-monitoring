# set_rtc.py — One-time helper to program the DS3231.
#
# You enter Amsterdam LOCAL time below; the script converts to UTC and stores
# UTC on the DS3231. The logger handles the UTC->Amsterdam conversion
# (including DST) at runtime, so you never need to re-set the clock for
# daylight saving.
#
# HOW TO USE:
#   1. Look at your Mac's clock (Amsterdam local time).
#   2. Edit the SET_TO_LOCAL tuple below to about 30-60 s in the future.
#   3. Save, then click Run in Thonny / MicroPico the moment that time hits.
#   4. Check the printed readback — it should say the RTC now reports the
#      correct UTC and Amsterdam times.
#
# Tuple fields: (year, month, day, hour, minute, second) — AMSTERDAM LOCAL

from machine import I2C, Pin
import ds3231
import tz
import time

# ---- EDIT ME right before running ----
SET_TO_LOCAL = (2026, 4, 20, 10, 32, 0)   # Amsterdam local time
# --------------------------------------

I2C_SDA_PIN = 0
I2C_SCL_PIN = 1

i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=100_000)
print("I2C devices found:", [hex(a) for a in i2c.scan()])

rtc = ds3231.DS3231(i2c)

# Convert local -> UTC before storing
utc_tuple = tz.amsterdam_to_utc(*SET_TO_LOCAL)
print("Input local (Ams): {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*SET_TO_LOCAL))
print("Storing UTC:       {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*utc_tuple))

rtc.set_datetime(*utc_tuple)

# Read back to confirm
time.sleep(1)
utc_back   = rtc.datetime()
local_back = tz.utc_to_amsterdam(*utc_back)
print("DS3231 UTC now:    {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*utc_back))
print("Interpreted (Ams): {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*local_back))
print("DS3231 temperature:", rtc.temperature(), "C")
