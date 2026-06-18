# tz.py — Amsterdam <-> UTC conversion with automatic DST handling.
#
# The DS3231 is programmed in UTC so we never have to re-set it twice a year.
# Filenames and the recording window (07:00-19:00) are in Amsterdam local time.
#
# European Summer Time:
#   - Starts: last Sunday of March,  01:00 UTC  (local jumps 02:00 -> 03:00)
#   - Ends:   last Sunday of October, 01:00 UTC (local falls 03:00 -> 02:00)
#
# Precomputed table of (DST_start_month, DST_start_day, DST_end_month, DST_end_day)
# at 01:00 UTC. Covers 2025-2035. Fallback for years outside the table assumes
# "DST between April and September" which is wrong by a few days but never by
# a whole month.

import time

# time.mktime takes 8-tuple on MicroPython, 9-tuple on CPython.
try:
    time.mktime((2000, 1, 1, 0, 0, 0, 0, 0))
    _MKTIME_SIZE = 8
except (TypeError, ValueError, OverflowError):
    _MKTIME_SIZE = 9


def _mktime(y, mo, d, h, mi, s):
    if _MKTIME_SIZE == 8:
        return _mktime(y, mo, d, h, mi, s)
    return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))


_DST_TABLE = {
    2025: (3, 30, 10, 26),
    2026: (3, 29, 10, 25),
    2027: (3, 28, 10, 31),
    2028: (3, 26, 10, 29),
    2029: (3, 25, 10, 28),
    2030: (3, 31, 10, 27),
    2031: (3, 30, 10, 26),
    2032: (3, 28, 10, 31),
    2033: (3, 27, 10, 30),
    2034: (3, 26, 10, 29),
    2035: (3, 25, 10, 28),
}


def _in_dst_utc(y, mo, d, h):
    """Given a UTC (y,mo,d,h), return True if Amsterdam is on CEST (+2)."""
    entry = _DST_TABLE.get(y)
    if entry is None:
        return 4 <= mo <= 9
    s_mo, s_d, e_mo, e_d = entry
    # Compare (mo, d, h) against (s_mo, s_d, 1) and (e_mo, e_d, 1).
    cur = (mo, d, h)
    start = (s_mo, s_d, 1)
    end = (e_mo, e_d, 1)
    return start <= cur < end


def utc_to_amsterdam(y, mo, d, h, mi, s):
    """Convert a UTC datetime tuple to Amsterdam local time."""
    offset = 2 if _in_dst_utc(y, mo, d, h) else 1
    t = _mktime(y, mo, d, h, mi, s)
    t += offset * 3600
    lt = time.localtime(t)
    return (lt[0], lt[1], lt[2], lt[3], lt[4], lt[5])


def amsterdam_to_utc(y, mo, d, h, mi, s):
    """Convert an Amsterdam local datetime tuple to UTC.

    During the ambiguous fall-back hour (02:00-02:59 local occurs twice), we
    assume the first occurrence (still on CEST). Good enough for setting the
    clock — don't set it at 02:30 local on the last Sunday of October.
    """
    # We need to know if the *local* time is in DST. Try CEST first (offset 2);
    # if the resulting UTC still falls in DST, use 2, else use 1.
    guess_utc_t = _mktime(y, mo, d, h, mi, s) - 2 * 3600
    glt = time.localtime(guess_utc_t)
    if _in_dst_utc(glt[0], glt[1], glt[2], glt[3]):
        offset = 2
    else:
        offset = 1
    t = _mktime(y, mo, d, h, mi, s) - offset * 3600
    lt = time.localtime(t)
    return (lt[0], lt[1], lt[2], lt[3], lt[4], lt[5])
