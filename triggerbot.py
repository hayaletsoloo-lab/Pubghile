# ============================================================
# PUBG Mobile Trigger Bot - Android ADB Input Automation
# Target  : PUBG Mobile (Android emulator / ADB-connected device)
# Method  : Screen color sampling via ADB screencap → pixel
#            analysis → automated tap injection
# Platform: Python 3.x + ADB (Windows/Linux host)
# Arch    : External — no memory read, pure I/O loop
# Author  : for Kuroko, brody session 😭
# ============================================================

import subprocess
import struct
import time
import sys
import os
from io import BytesIO

# ─── CONFIG ────────────────────────────────────────────────
ADB_PATH        = "adb"           # adb in PATH or full path
DEVICE_SERIAL   = None            # None = first device; set "emulator-5554" etc.

# Screen region to sample (center crosshair area)
# Adjust to your resolution — 1920x1080 default
SAMPLE_X        = 960             # center X
SAMPLE_Y        = 540             # center Y
SAMPLE_RADIUS   = 6               # pixel scan radius (NxN box)

# Enemy highlight color range (PUBG enemy red highlight / hit marker)
# Tune these RGB ranges to match enemy outline shader color
TARGET_R_MIN    = 180
TARGET_R_MAX    = 255
TARGET_G_MIN    = 0
TARGET_G_MAX    = 80
TARGET_B_MIN    = 0
TARGET_B_MAX    = 80

# Tap coordinates (fire button on screen)
FIRE_X          = 980
FIRE_Y          = 600

# Timing
SCAN_INTERVAL   = 0.03            # seconds between scans (~33 fps)
TAP_DURATION_MS = 50              # tap hold duration

# ─── ADB HELPERS ────────────────────────────────────────────

def adb_cmd(args: list) -> bytes:
    """Run adb command, return raw stdout bytes."""
    cmd = [ADB_PATH]
    if DEVICE_SERIAL:
        cmd += ["-s", DEVICE_SERIAL]
    cmd += args
    result = subprocess.run(cmd, capture_output=True)
    return result.stdout


def grab_screencap() -> bytes:
    """Pull raw PNG screencap bytes from device."""
    return adb_cmd(["exec-out", "screencap", "-p"])


def parse_png_pixels(png_bytes: bytes, x: int, y: int, radius: int) -> list:
    """
    Minimal PNG parser — extracts RGB tuples from a region.
    We only need a small box, so we use Python's built-in zlib
    to decompress IDAT chunks without PIL.
    """
    import zlib

    buf = BytesIO(png_bytes)

    # ── PNG signature check ──
    sig = buf.read(8)
    if sig != b'\x89PNG\r\n\x1a\n':
        raise ValueError("Not a PNG")

    width = height = bit_depth = color_type = 0
    idat_data = b''

    # ── Walk chunks ──
    while True:
        chunk_header = buf.read(8)
        if len(chunk_header) < 8:
            break
        length = struct.unpack('>I', chunk_header[:4])[0]
        chunk_type = chunk_header[4:8]
        data = buf.read(length)
        buf.read(4)  # CRC

        if chunk_type == b'IHDR':
            width, height = struct.unpack('>II', data[:8])
            bit_depth = data[8]
            color_type = data[9]

        elif chunk_type == b'IDAT':
            idat_data += data

        elif chunk_type == b'IEND':
            break

    # ── Decompress ──
    raw = zlib.decompress(idat_data)

    # color_type 2 = RGB, 6 = RGBA
    if color_type == 2:
        channels = 3
    elif color_type == 6:
        channels = 4
    else:
        raise ValueError(f"Unsupported PNG color type: {color_type}")

    stride = width * channels + 1  # +1 for filter byte per row

    # ── Extract region pixels ──
    pixels = []
    x_start = max(0, x - radius)
    x_end   = min(width  - 1, x + radius)
    y_start = max(0, y - radius)
    y_end   = min(height - 1, y + radius)

    for row in range(y_start, y_end + 1):
        row_offset = row * stride + 1   # skip filter byte
        for col in range(x_start, x_end + 1):
            px_offset = row_offset + col * channels
            r = raw[px_offset]
            g = raw[px_offset + 1]
            b = raw[px_offset + 2]
            pixels.append((r, g, b))

    return pixels


def pixel_matches_enemy(r: int, g: int, b: int) -> bool:
    """Check if pixel falls within enemy highlight color range."""
    return (
        TARGET_R_MIN <= r <= TARGET_R_MAX and
        TARGET_G_MIN <= g <= TARGET_G_MAX and
        TARGET_B_MIN <= b <= TARGET_B_MAX
    )


def tap(x: int, y: int, duration_ms: int = 50):
    """Send a tap event via ADB input swipe (swipe from→to same coord = tap)."""
    adb_cmd([
        "shell", "input", "swipe",
        str(x), str(y), str(x), str(y),
        str(duration_ms)
    ])


# ─── MAIN LOOP ───────────────────────────────────────────────

def run():
    print("[*] PUBG Mobile Trigger Bot — ADB edition 👆")
    print(f"[*] Sampling ({SAMPLE_X}, {SAMPLE_Y}) +/-{SAMPLE_RADIUS}px")
    print(f"[*] Fire tap -> ({FIRE_X}, {FIRE_Y})")
    print("[*] Press CTRL+C to stop\n")

    shot_count   = 0
    scan_count   = 0
    last_fire_ts = 0.0

    while True:
        loop_start = time.perf_counter()

        # 1. Grab screen
        try:
            png_data = grab_screencap()
        except Exception as e:
            print(f"[!] screencap failed: {e}")
            time.sleep(0.1)
            continue

        if not png_data or len(png_data) < 100:
            time.sleep(SCAN_INTERVAL)
            continue

        # 2. Sample pixels in crosshair region
        try:
            pixels = parse_png_pixels(png_data, SAMPLE_X, SAMPLE_Y, SAMPLE_RADIUS)
        except Exception as e:
            print(f"[!] PNG parse error: {e}")
            time.sleep(SCAN_INTERVAL)
            continue

        scan_count += 1

        # 3. Count matching pixels
        matches = sum(1 for r, g, b in pixels if pixel_matches_enemy(r, g, b))
        total   = len(pixels)
        ratio   = matches / total if total else 0

        # 4. Trigger threshold (20% of sampled region = enemy color)
        if ratio >= 0.20:
            now = time.perf_counter()
            # Cooldown: don't spam faster than ~100ms between shots
            if now - last_fire_ts >= 0.1:
                tap(FIRE_X, FIRE_Y, TAP_DURATION_MS)
                last_fire_ts = now
                shot_count  += 1
                print(f"[FIRE] scan#{scan_count} ratio={ratio:.2f} shots={shot_count}")

        # 5. Maintain scan rate
        elapsed = time.perf_counter() - loop_start
        sleep   = max(0.0, SCAN_INTERVAL - elapsed)
        time.sleep(sleep)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n[*] Stopped. Peace")
        sys.exit(0)
