# ds3231.py
# Minimal MicroPython driver for the DS3231 I2C real-time clock.
# Provides just what the logger needs: set time, read time.
#
# Datasheet registers (BCD):
#   0x00 seconds, 0x01 minutes, 0x02 hours,
#   0x03 weekday, 0x04 day, 0x05 month (bit7 = century),
#   0x06 year (00-99)

from machine import I2C

DS3231_ADDR = 0x68


def _bcd2dec(b):
    return (b >> 4) * 10 + (b & 0x0F)


def _dec2bcd(d):
    return ((d // 10) << 4) | (d % 10)


class DS3231:
    def __init__(self, i2c, addr=DS3231_ADDR):
        self.i2c = i2c
        self.addr = addr
        if addr not in i2c.scan():
            raise OSError("DS3231 not found on I2C bus")

    def datetime(self):
        """Return (year, month, day, hour, minute, second). Year is 4-digit."""
        buf = self.i2c.readfrom_mem(self.addr, 0x00, 7)
        sec = _bcd2dec(buf[0] & 0x7F)
        minute = _bcd2dec(buf[1] & 0x7F)
        # Force 24h: if bit 6 of hours reg is set, it's 12h mode -> convert.
        hour_reg = buf[2]
        if hour_reg & 0x40:
            # 12h mode
            hour = _bcd2dec(hour_reg & 0x1F)
            if hour_reg & 0x20:  # PM bit
                if hour != 12:
                    hour += 12
            else:
                if hour == 12:
                    hour = 0
        else:
            hour = _bcd2dec(hour_reg & 0x3F)
        day = _bcd2dec(buf[4] & 0x3F)
        month = _bcd2dec(buf[5] & 0x1F)
        year = 2000 + _bcd2dec(buf[6])
        return (year, month, day, hour, minute, sec)

    def set_datetime(self, year, month, day, hour, minute, second, weekday=1):
        """Set the RTC. year may be 2- or 4-digit. weekday 1..7 (doesn't matter for us)."""
        if year >= 2000:
            year -= 2000
        buf = bytearray(7)
        buf[0] = _dec2bcd(second)
        buf[1] = _dec2bcd(minute)
        buf[2] = _dec2bcd(hour)  # 24h mode (bit 6 = 0)
        buf[3] = _dec2bcd(weekday)
        buf[4] = _dec2bcd(day)
        buf[5] = _dec2bcd(month)  # century bit stays 0 (we're in 2000s)
        buf[6] = _dec2bcd(year)
        self.i2c.writeto_mem(self.addr, 0x00, buf)

    def temperature(self):
        """Return DS3231 internal temperature in Celsius (0.25 C resolution)."""
        buf = self.i2c.readfrom_mem(self.addr, 0x11, 2)
        msb = buf[0]
        lsb = buf[1]
        # Two's complement for MSB (signed)
        if msb & 0x80:
            msb -= 256
        return msb + (lsb >> 6) * 0.25
