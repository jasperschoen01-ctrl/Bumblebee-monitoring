from machine import Pin, I2S

print("Setting up I2S...")
mic = I2S(1, 
          sck=Pin(10), 
          ws=Pin(11), 
          sd=Pin(12),
          mode=I2S.RX, 
          bits=32, 
          format=I2S.MONO, 
          rate=16000, 
          ibuf=10000)

buf = bytearray(256) # Very small buffer

os.mount(sd, "/sd")
print("SD card mounted, free space:", os.statvfs('/sd')[0] * os.statvfs('/sd')[3], "bytes")

try:
    print("Attempting to read from microphone...")
    # This is where it will freeze if hardware is failing
    bytes_read = mic.readinto(buf) 
    print(f"Success! Read {bytes_read} bytes.")
    print("Raw data sample:", buf[:10])
except Exception as e:
    print("Error:", e)
finally:
    mic.deinit()
    print("I2S Deinitialized.")
