from smbus2 import SMBus
import time
from datetime import datetime
import pytz
import sys
import select
import json
import os
from enum import Enum, auto
import subprocess
import re

class ClockSettings:
    def __init__(self, filename="clock_settings.json"):
        self.filename = filename
        self.brightness = 0
        self.timezone = "America/Chicago"
        self.hour_24 = False
        self.flash_colon = True
        self.load()

    def load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r") as f:
                    data = json.load(f)
                self.brightness = data.get("brightness", 0)
                self.timezone = data.get("timezone", "America/Chicago")
                self.hour_24 = data.get("hour_24", False)
                self.flash_colon = data.get("flash_colon", True)
            except Exception as e:
                print(f"Error loading settings: {e}. Using defaults.")
                self.save()
        else:
            self.save()

    def save(self):
        data = {
            "brightness": self.brightness,
            "timezone": self.timezone,
            "hour_24": self.hour_24,
            "flash_colon": self.flash_colon
        }
        try:
            with open(self.filename, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def increase_brightness(self):
        self.brightness += 1
        if self.brightness > 15:
            self.brightness = 15
        #self.save()

    def decrease_brightness(self):
        self.brightness -= 1
        if self.brightness < 0:
            self.brightness = 0
        #self.save()

class Display:
    """
    HT16K33 4-digit 7-seg (Adafruit-style) with center colon.
    - Digits are 1 byte each.
    - Colon is controlled via RAM address 0x04 (0x02 turns it on).
    """

    # RAM addresses for each of the 4 digits (wired this way on the Adafruit backpack)
    _DIG_ADDR = [0x00, 0x02, 0x06, 0x08]
    _COLON_ADDR = 0x04  # write 0x02 to turn colon on, 0x00 to turn it off

    # 7-seg glyphs (bit layout matches the backpack’s wiring)
    # NOTE: dp (decimal point) bit is 0x80 on the same digit byte.
    _SEG7 = {
        ' ': 0x00, '-': 0x40, '_': 0x08,
        '0': 0x3F, '1': 0x06, '2': 0x5B, '3': 0x4F,
        '4': 0x66, '5': 0x6D, '6': 0x7D, '7': 0x07,
        '8': 0x7F, '9': 0x6F,

        # Letters good enough for menus (“FLASH”, “ZONE”, “WIFI”, “IP”, “24H”)
        'A': 0x77, 'a': 0x5F,
        'b': 0x7C, 'C': 0x39, 'c': 0x58,
        'd': 0x5E, 'E': 0x79, 'e': 0x7B,
        'F': 0x71, 'H': 0x76, 'h': 0x74,
        'I': 0x06, 'i': 0x04, 'J': 0x1E,
        'L': 0x38, 'n': 0x54, 'o': 0x5C,
        'P': 0x73, 'r': 0x50, 'S': 0x6D,
        't': 0x78, 'U': 0x3E, 'Y': 0x6E,
        'Z': 0x5B,  # same as '2' visually
        # You can add more as needed
    }

    def __init__(self, bus=1, address=0x70, brightness=0, scroll_delay=0.25):
        self.bus = bus
        self.address = address
        self.scroll_delay = float(scroll_delay)
        self.colon = True
        self._init_hw(brightness)

    # ---- Low-level helpers ----
    def _init_hw(self, brightness):
        from smbus2 import SMBus
        with SMBus(self.bus) as i2c:
            # Oscillator ON
            i2c.write_byte(self.address, 0x21)
            # Display ON, blink OFF
            i2c.write_byte(self.address, 0x81)
            # Brightness (0xE0..0xEF)
            i2c.write_byte(self.address, 0xE0 | (int(brightness) & 0x0F))
            self.clear()

    def clear(self):
        from smbus2 import SMBus
        with SMBus(self.bus) as i2c:
            i2c.write_i2c_block_data(self.address, 0x00, [0x00] * 16)

    def set_brightness(self, level):
        from smbus2 import SMBus
        level = max(0, min(15, int(level)))
        with SMBus(self.bus) as i2c:
            i2c.write_byte(self.address, 0xE0 | level)

    def _encode_char_7seg(self, ch):
        # Accept both ASCII and ints for convenience
        if isinstance(ch, int) and 0 <= ch <= 9:
            ch = str(ch)
        return self._SEG7.get(ch, 0x00)

    def _write_four_chars(self, s4):
        """
        Write exactly 4 characters + colon in one go.
        s4 is padded/truncated to 4.
        """
        from smbus2 import SMBus

        s4 = (s4 + "    ")[:4]
        # Build a 16-byte frame starting at 0x00
        frame = [0x00] + [0x00] * 16

        # Digits
        for pos, ch in enumerate(s4):
            glyph = self._encode_char_7seg(ch)
            addr = self._DIG_ADDR[pos]
            frame[1 + addr] = glyph

        # Colon
        frame[1 + self._COLON_ADDR] = 0x02 if self.colon else 0x00

        with SMBus(self.bus) as i2c:
            i2c.write_i2c_block_data(self.address, frame[0], frame[1:])

    # ---- Public API ----
    def display(self, text):
        """
        Show a string. If length <= 4: display immediately.
        If length > 4: scroll left across 4 digits, then leave
        the final 4 characters showing.
        """
        import time
        if not isinstance(text, str):
            text = str(text)

        n = len(text)
        if n <= 4:
            self._write_four_chars(text)
            return

        for i in range(0, n - 4 + 1):
            self._write_four_chars(text[i:i+4])
            if i < n - 4:
                time.sleep(self.scroll_delay)


def display_time(disp, settings):
    # Get timezone-aware current time
    tz = pytz.timezone(settings.timezone)
    now = datetime.now(tz)
    minute = now.minute
    hour = now.hour
    if not settings.hour_24:
        hour = hour % 12
        if hour == 0:
            hour = 12
    
    # Handle colon flashing
    if settings.flash_colon:
        disp.colon = (now.second % 2 == 0)
    else:
        disp.colon = True
    
    # Pad hour with space, minute with zero
    disp.display(f"{hour:2d}{minute:02d}")

def is_dark_outside(latitude=30.0, longitude=-97.8):
    """
    Estimate if it is dark outside using sunrise/sunset times.
    Defaults to Central Texas coordinates.
    """
    from datetime import datetime
    import pytz
    import math

    # approximate solar calculation using NOAA formula
    # https://gml.noaa.gov/grad/solcalc/
    def solar_declination(day_of_year):
        return math.radians(23.44) * math.sin(math.radians(360 / 365 * (day_of_year - 81)))

    def hour_angle(latitude, decl):
        lat_rad = math.radians(latitude)
        return math.degrees(math.acos(-math.tan(lat_rad) * math.tan(decl)))

    tz = pytz.timezone("America/Chicago")
    now = datetime.now(tz)
    day_of_year = now.timetuple().tm_yday
    decl = solar_declination(day_of_year)
    ha = hour_angle(latitude, decl)
    daylight_hours = 2 * ha / 15  # convert degrees to hours (15° = 1h)

    solar_noon = 12.0
    sunrise = solar_noon - daylight_hours / 2
    sunset = solar_noon + daylight_hours / 2
    local_time = now.hour + now.minute / 60

    return not (sunrise <= local_time <= sunset)


if __name__ == "__main__":

    # Load settings
    settings = ClockSettings()
    disp = Display(bus=1, address=0x70, brightness=settings.brightness, scroll_delay=0.5)
    last_minute = -1

    try:
        while True:
            disp.colon = settings.flash_colon and (int(time.time()) % 2 == 0)
            display_time(disp, settings)

            now = datetime.now(pytz.timezone(settings.timezone))
            # Check once per minute on the first second
            if now.second == 0 and now.minute != last_minute:
                last_minute = now.minute
                dark = is_dark_outside()
                if settings.brightness == 0 and not dark:
                    settings.brightness = 15
                    disp.set_brightness(15)
                elif settings.brightness == 15 and dark:
                    settings.brightness = 0
                    disp.set_brightness(0)

            now = time.time()
            sleep_time = 1 - (now % 1)
            time.sleep(sleep_time)                        
    except KeyboardInterrupt:
        print("\nExiting")
    finally:
        disp.clear()

