from machine import SPI, Pin
import sdcard
import os

print("Starting SD test...")

spi = SPI(1,
          baudrate=400000,
          sck=Pin(14),
          mosi=Pin(15),
          miso=Pin(12))

print("SPI created ok")

sd = sdcard.SDCard(spi, Pin(13))
print("SDCard object created ok")

os.mount(sd, "/sd")
print("Mounted ok")

print("Files:", os.listdir("/sd"))