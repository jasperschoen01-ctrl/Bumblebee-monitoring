from machine import I2S, Pin
import struct

# --- Config ---
WS_PIN   = 19   # LRCL
SD_PIN   = 20    # DOUT
SCK_PIN  = 18   # BCLK
SAMPLE_RATE   = 11025
RECORD_SECONDS = 5
BITS          = 32
NUM_SAMPLES    = SAMPLE_RATE * RECORD_SECONDS
DISCARD_SECS   = 0.5  # seconds to discard at start
DISCARD_SAMPLES = int(SAMPLE_RATE * DISCARD_SECS)
FILENAME       = "recording.wav"

# --- WAV header ---
def write_wav_header(file, sample_rate, num_samples):
    data_size = num_samples * 2
    file.write(b'RIFF')
    file.write(struct.pack('<I', 36 + data_size))
    file.write(b'WAVE')
    file.write(b'fmt ')
    file.write(struct.pack('<I', 16))
    file.write(struct.pack('<H', 1))
    file.write(struct.pack('<H', 1))
    file.write(struct.pack('<I', sample_rate))
    file.write(struct.pack('<I', sample_rate * 2))
    file.write(struct.pack('<H', 2))
    file.write(struct.pack('<H', 16))
    file.write(b'data')
    file.write(struct.pack('<I', data_size))

# --- I2S setup ---
audio_in = I2S(
    1,
    sck=Pin(SCK_PIN),
    ws=Pin(WS_PIN),
    sd=Pin(SD_PIN),
    mode=I2S.RX,
    bits=BITS,
    format=I2S.MONO,
    rate=SAMPLE_RATE,
    ibuf=8192
)

# --- Step 1: Discard first 0.5 seconds ---
print("Stabilising mic...")
discard_buf = bytearray(DISCARD_SAMPLES * 4)    # allocate buffer for 5512 samples * 4 bytes each
audio_in.readinto(discard_buf)                  # read 0.5 seconds of audio into it and throw it away
print("Recording", RECORD_SECONDS, "seconds...")

# --- Step 2: Read ALL audio into RAM ---
raw_buf = bytearray(NUM_SAMPLES * 4)  # pre-allocate one big buffer for ALL audio — 33075 * 4 = ~132KB
chunk   = bytearray(512 * 4)          # temporary chunk to read 512 samples at a time
offset  = 0                           # tracks how far through raw_buf we have filled

while offset < len(raw_buf):
    remaining = len(raw_buf) - offset
    to_read   = min(len(chunk), remaining)
    num_read  = audio_in.readinto(memoryview(chunk)[:to_read])
    raw_buf[offset:offset + num_read] = chunk[:num_read]
    offset += num_read

audio_in.deinit()
print("Recording done, saving to flash...")

# --- Step 3: Compute DC offset from raw buffer ---
# Sample every 10th value to estimate DC without unpacking everything
num_check = NUM_SAMPLES // 10
dc_sum    = 0
for i in range(num_check):
    idx     = i * 10 * 4  # every 10th sample, 4 bytes each
    sample  = struct.unpack('<i', raw_buf[idx:idx + 4])[0]
    dc_sum += sample >> 14
dc_offset = dc_sum // num_check
print("DC offset estimated:", dc_offset)

# --- Step 4: Convert and write to flash ---
with open(FILENAME, 'wb') as f:
    write_wav_header(f, SAMPLE_RATE, NUM_SAMPLES)

    chunk_size = 256
    bytes_each = 4

    for i in range(0, NUM_SAMPLES, chunk_size):
        start = i * bytes_each
        end   = min(start + chunk_size * bytes_each, len(raw_buf))
        batch = struct.unpack('<' + 'i' * ((end - start) // 4), raw_buf[start:end])

        out = bytearray()
        for s in batch:
            s16 = (s >> 14) - dc_offset  # remove DC offset
            s16 = max(-32768, min(32767, s16))
            out += struct.pack('<h', s16)
        f.write(out)

print("Saved:", FILENAME)