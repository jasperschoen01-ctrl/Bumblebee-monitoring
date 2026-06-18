# Bumblebee Audio Logger — Raspberry Pi Pico 2 W

Continuous audio logger that writes one WAV file per minute to an SD card,
timestamped from a battery-backed DS3231 real-time clock.

- **Sample rate:** 11 025 Hz, 16-bit mono
- **File length:** ~60 s. Files are closed when the DS3231 ticks over to a
  new minute, so filenames always line up with wall-clock minutes.
- **Filename:** `YYMMDD_HHMM00_Pico.WAV` in **Amsterdam local time** (DST
  handled automatically; clock stays in UTC internally).
- **Recording window:** 07:00–19:00 Amsterdam local. Outside that window
  the Pico stays awake, keeps the mic running, and just discards samples.
- **Click handling:** I2S is started once at boot and the first 0.7 s of
  samples are discarded. After that the mic is never stopped, so there is
  no click at any file boundary.

## Files

| File          | Purpose                                                     |
|---------------|-------------------------------------------------------------|
| `main.py`     | Auto-runs on boot. Records 07–19 Amsterdam.                 |
| `selftest.py` | Manual run-once diagnostic. Records `/sd/selftest.WAV`.     |
| `set_rtc.py`  | Run once from Thonny / MicroPico to program the DS3231.     |
| `tz.py`       | Amsterdam ↔ UTC conversion with auto-DST.                   |
| `ds3231.py`   | Minimal I2C driver for the DS3231 RTC.                      |
| `sdcard.py`   | Standard MicroPython SPI SD card driver.                    |
| `blink.py`    | Leftover test script, not used by the logger.               |

## Hardware wiring

```
SPH0645LM4H  ──►  Pico
  VDD         ─►  3V3 (pin 36)
  GND         ─►  GND
  SEL         ─►  GND        (left channel)
  LRCL        ─►  GP19
  BCLK        ─►  GP18
  DOUT        ─►  GP20

micro-SD (SPI0) ──►  Pico
  VCC         ─►  3V3   (modules with onboard regulator: use VBUS / 5V)
  GND         ─►  GND
  SCK         ─►  GP2
  MOSI        ─►  GP3
  MISO        ─►  GP4
  CS          ─►  GP5

DS3231 (I2C0) ──►  Pico
  VCC         ─►  3V3
  GND         ─►  GND
  SDA         ─►  GP0
  SCL         ─►  GP1
  (CR2032 on board keeps it ticking when Pico is off)
```

All three devices run on 3V3. The SPH0645 needs `SEL` tied to **GND** so it
drives data on the left channel (which is what MicroPython's `I2S.MONO`
reads).

## One-time setup

1. Flash MicroPython (RP2 build) onto the Pico.
2. Copy every file in this folder (`main.py`, `selftest.py`, `set_rtc.py`,
   `tz.py`, `ds3231.py`, `sdcard.py`) to the Pico's filesystem using Thonny
   or VS Code with the MicroPico extension.
3. Insert a FAT32-formatted micro-SD card (32 GB or smaller is safest).
4. **Set the clock.** Open `set_rtc.py`, edit `SET_TO_LOCAL` to an
   Amsterdam local time about 30 s into the future, hit Run at the moment
   that time hits on your Mac. Confirm the readback.
5. **Run the self-test.** Open `selftest.py` and click Run. It will mount
   the SD, read the DS3231, and record a 3-second WAV to
   `/sd/selftest.WAV`. Pop the card into your Mac to listen.
6. Unplug. Replug. `main.py` runs automatically.

## How do I know it's working?

Several overlapping signals:

**At boot, watch the LED.** You should see this exact sequence within ~3 s
of applying power:

| Blink                          | Meaning                              |
|--------------------------------|--------------------------------------|
| 1 long flash (~1 s)            | Pico powered, script started         |
| 2 short flashes                | SD card mounted                      |
| 3 short flashes                | DS3231 responding                    |
| 4 short flashes                | I2S started                          |
| 5 short flashes                | Logger ready, entering main loop     |

If the sequence stops after the long flash → SD card problem. Stops after
2 flashes → DS3231 problem (check wiring or battery). Stops after 3 → I2S
problem. Continuous rapid flashing forever = fatal error logged to
`/sd/boot.txt`.

**During operation:**

| Pattern                    | Meaning                                  |
|----------------------------|------------------------------------------|
| very brief flash           | a file just closed (inside window)       |
| slow triple-blink / 10 s   | outside the recording window, idling     |
| continuous rapid flashing  | fatal error — check `/sd/boot.txt`       |

So: inside the window you'll see a subtle blink once per minute; outside
you'll see a distinctive triple-blink every 10 s. Either way, if the LED
is doing *something*, the device is alive.

**Plug into your Mac and watch the serial output.** Thonny shows the
REPL; MicroPico shows it in the VS Code terminal. You'll see lines like:

```
=== Bumblebee logger booting ===
DS3231 UTC = (2026, 4, 20, 8, 30, 0)
Logger ready. Window: 07:00 - 19:00 Amsterdam
[10:30] open /sd/260420_103000_Pico.WAV
[10:31] open /sd/260420_103100_Pico.WAV
...
```

**Check the SD card.** Pop it out and mount it on your Mac. You should
see:

- `260420_HHMM00_Pico.WAV` files, one per minute, each ~1.3 MB.
- `boot.txt` — one line per boot.
- `errors.txt` — empty, unless something went wrong.

Open any WAV in QuickTime / Audacity. Should play ~60 s of sound.

## Storage math

At 11 025 Hz × 16-bit mono, one file ≈ **1.3 MB**. That's 77 MB/hour; at
12 h of recording (07–19) that's ~920 MB/day. A 32 GB card gives you
about 35 days of recording windows.

## Notes on the SPH0645

- It's a 24-bit mic delivered in a 32-bit I2S slot, left-justified. We
  right-shift by 14 bits to fit into a signed 16-bit WAV sample, which
  gives a modest gain boost suitable for moderate-level bumblebee
  recordings. If you get clipping, change `GAIN_SHIFT = 14` in `main.py`
  to `16`.
- The mic has a known DC offset / low-frequency droop. If it bothers
  your downstream analysis, apply a high-pass filter (≥100 Hz) in Python
  after pulling the files off the card — the existing
  `src/audio_analysis/` notebooks already do this.

## Time zone / DST notes

- The DS3231 is programmed in UTC. `set_rtc.py` does the UTC conversion
  for you — you enter Amsterdam local time.
- `tz.py` contains a DST table for 2025–2035. Inside that range, UTC →
  Amsterdam conversion is exact. Outside the range it falls back to
  "DST between April and September", which is correct to within a few
  days.
- You don't need to re-set the RTC when DST changes; the logger just
  picks up the new offset automatically.

## What happens when the card fills up

The firmware throws `OSError` on every write; the watchdog reboots the
Pico every 8 s, and errors land in `/sd/errors.txt`. Plan the card size
for your deployment window.

## Tuning

Edit the constants at the top of `main.py`:

- `RECORD_START_HOUR` / `RECORD_STOP_HOUR` — the recording window.
- `SAMPLE_RATE`    — raise to 22050 or 44100 if you need more headroom.
  Also expect larger files and more SD-write pressure.
- `GAIN_SHIFT`     — 16 = native, 14 = +12 dB, 12 = +24 dB (will clip).
- `CHUNK_SAMPLES`  — bigger = fewer writes, more RAM. 512 is safe.
