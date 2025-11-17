from smbus2 import SMBus
import time
from datetime import datetime
import pytz
import sys
import select
import tty
import termios
import threading
import queue
import json
import os
from enum import Enum, auto
import subprocess
import re
from gpiozero import Device, Button, RotaryEncoder
from gpiozero.pins.pigpio import PiGPIOFactory

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
    # HT16K33 digit base addresses (two bytes per digit)
    _DIG_BASE = [0x00, 0x02, 0x04, 0x06]

    _SEG14 = {
        ' ':0x0000, '-':0x00C0, '[':0x0039, ']':0x000F, '_':0x0008,
        '0':0x003F, '1':0x0006, '2':0x00DB, '3':0x00CF, '4':0x00E6,
        '5':0x00ED, '6':0x00FD, '7':0x0007, '8':0x00FF, '9':0x00EF,
        '!':0x4002, '@':0x02BB, '#':0x12F8, '$':0x12ED,
        '%':0x0C24, '^':0x0120, '&':0x235D, '*':0x3FC0,
        '(':0x2400, ')':0x0900, ',':0x0800, '.':0x4000,
        '?':0x60A3, '/':0x0C00, '\\':0x2100, '~':0x0100,
        '=':0x00C8, '+':0x12C0, '{':0x0949, '}':0x2489,
        '|':0x1200, '<':0x0480, '>':0x0140, ':':0x0030,
        ';':0x0A00, '\'':0x0020, '"':0x0220, '↑':0x3800, '↓':0x0700,

        # common letters approximated on 7-seg:
        'A':0x00F7, 'a':0x00DF,
        'B':0x2479, 'b':0x00FC,
        'C':0x0039, 'c':0x00D8,
        'D':0x0930, 'd':0x00DE,
        'E':0x0079, 'e':0x00FB,
        'F':0x00F1, 'f':0x0071,
        'G':0x00BD, 'g':0x00EF,
        'H':0x00F6, 'h':0x00F4, 
        'I':0x1209, 'i':0x1200, 
        'J':0x001E, 'j':0x001E, 
        'K':0x2470, 'k':0x2470, 
        'L':0x0038, 'l':0x0018, 
        'M':0x0536, 'm':0x0536, 
        'N':0x2136, 'n':0x00D4, 
        'O':0x003F, 'o':0x00DC, 
        'P':0x00F3, 'p':0x00F3, 
        'Q':0x203F, 'q':0x20E3, 
        'R':0x20F3, 'r':0x0050, 
        'S':0x00ED, 's':0x00ED, 
        'T':0x1201, 't':0x12C0, 
        'U':0x003E, 'u':0x001C, 
        'V':0x0C30, 'v':0x0810, 
        'W':0x2836, 'w':0x2814, 
        'X':0x2D00, 'x':0x2D00, 
        'Y':0x1500, 'y':0x1500, 
        'Z':0x0C09, 'z':0x0848, 
    }

    def __init__(self, bus=1, address=0x70, brightness=0, scroll_delay=0.25, double_mid_chars=None):
        """
        :param bus: I2C bus number (your board uses bus 0)
        :param address: I2C address (default 0x70)
        :param brightness: 0..15
        :param scroll_delay: seconds between scroll steps
        :param double_mid_chars: iterable of chars that should force both middle bars (default: {'2','3','4'})
        """
        self.bus = bus
        self.address = address
        self.scroll_delay = float(scroll_delay)
        self.double_mid_chars = set(double_mid_chars or {'2', '3', '4'})
        self.colon = True  # Initialize colon state
        self._init_hw(brightness)

    # ---- Low-level helpers ----
    def _init_hw(self, brightness):
        with SMBus(self.bus) as i2c:
            # Oscillator ON
            i2c.write_byte(self.address, 0x21)
            # Display ON, blink OFF
            i2c.write_byte(self.address, 0x81)
            # Brightness (0xE0 .. 0xEF)
            i2c.write_byte(self.address, 0xE0 | (brightness & 0x0F))
            # Clear
            self.clear()

    def clear(self):
        with SMBus(self.bus) as i2c:
            i2c.write_i2c_block_data(self.address, 0x00, [0x00] * 16)

    def set_brightness(self, level):
        level = max(0, min(15, int(level)))
        with SMBus(self.bus) as i2c:
            i2c.write_byte(self.address, 0xE0 | level)

    def _encode_char_14seg(self, ch):
        """
        Return (low_byte, high_byte) for a given character.
        Uses the 14-segment value from _SEG14 and splits into two bytes.
        """
        val = self._SEG14.get(ch, 0x0000)
        low = val & 0xFF
        high = (val >> 8) & 0xFF
        return low, high

    def _write_four_chars(self, s4):
        """
        Write exactly 4 characters to the display RAM in one transaction.
        s4: length-4 string (or will be padded/truncated to 4)
        """
        s4 = (s4 + "    ")[:4]
        frame = [0x00] + [0x00] * 16
        for pos, ch in enumerate(s4):
            low, high = self._encode_char_14seg(ch)
            # Set colon bit (bit 15) in second character's high byte
            if pos == 1 and self.colon:
                high = high | 0x40  # Set bit 7 (15th bit of character) for colon
            base = self._DIG_BASE[pos]
            frame[1 + base]     = low
            frame[1 + base + 1] = high
            

        with SMBus(self.bus) as i2c:
            i2c.write_i2c_block_data(self.address, frame[0], frame[1:])

    # ---- Public API ----
    def display(self, text):
        """
        Show a string. If length <= 4: display immediately.
        If length > 4: scroll left one character at a time across 4 digits,
        then stop with the **last 4 characters** visible.
        """
        if not isinstance(text, str):
            text = str(text)

        n = len(text)
        if n <= 4:
            self._write_four_chars(text)
            return

        # Scroll window from 0..(n-4)
        for i in range(0, n - 4 + 1):
            self._write_four_chars(text[i:i+4])
            # If this is the final frame, stop without delay so it "ends" held on screen
            if i < n - 4:
                time.sleep(self.scroll_delay)

        # Final frame (last 4 chars) is already shown and remains displayed

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

def keyboard_thread_func(q):
    while True:
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            seq = ch + sys.stdin.read(2)
            if seq == '\x1b[A':
                q.put("up")
            elif seq == '\x1b[B':
                q.put("down")
            elif seq == '\x1b[C':
                q.put("right")
            elif seq == '\x1b[D':
                q.put("left")
        elif ch in ('\n', '+', '-'):
            q.put(ch)

def set_flash(disp, settings, input_queue):
    while True:
        if settings.flash_colon:
            status = "On"
        else:
            status = "Off"
        disp.display(f"FLASH  {status}")
        while input_queue.empty():
            time.sleep(0.1)
        input = input_queue.get()
        if input == 'up' or input == 'down' or input == 'right' or input == 'left':
            settings.flash_colon = not settings.flash_colon
        elif input == '\n':
            settings.save()
            return

def set_24h(disp, settings, input_queue):
    while True:
        if settings.hour_24:
            status = "24"
        else:
            status = "12"
        disp.display(f"{status}")
        while input_queue.empty():
            time.sleep(0.1)
        input = input_queue.get()
        if input == 'up' or input == 'down' or input == 'right' or input == 'left':
            settings.hour_24 = not settings.hour_24
        elif input == '\n':
            settings.save()
            return

def set_timezone(disp, settings, input_queue):
    zones = {
        "Eastern": "America/New_York",
        "Central": "America/Chicago",
        "Mountain": "America/Denver",
        "Pacific": "America/Los_Angeles",
        "Alaska": "America/Anchorage",
        "Hawaii": "America/Honolulu",
        "UTC": "UTC"
    }
    index = list(zones.values()).index(settings.timezone)
    while True:
        disp.display(f"{list(zones.keys())[index]}")
        while input_queue.empty():
            time.sleep(0.1)
        input = input_queue.get()
        if input == 'up' or input == 'right':
            index = (index + 1) % len(zones)
            settings.timezone = list(zones.values())[index]
        elif input == 'down' or input == 'left':
            index = (index - 1) % len(zones)
            settings.timezone = list(zones.values())[index]
        elif input == '\n':
            settings.save()
            return

def set_wifi(disp, settings, input_queue):
    symbols = {"a": "↓A a", "b": "↓B b", "c": "↓C c", "d": "↓D d",
               "e": "↓E e", "f": "↓F f", "g": "↓G g", "h": "↓H h",
               "i": "↓I i", "j": "↓J j", "k": "↓K k", "l": "↓L l",
               "m": "↓M m", "n": "↓N n", "o": "↓O o", "p": "↓P p",
               "q": "↓Q q", "r": "↓R r", "s": "↓S s", "t": "↓T t",
               "u": "↓U u", "v": "↓V v", "w": "↓W w", "x": "↓X x",
               "y": "↓Y y", "z": "↓Z z",
               "1": "N  1", "2": "N  2", "3": "N  3", "4": "N  4",
               "5": "N  5", "6": "N  6", "7": "N  7", "8": "N  8",
               "9": "N  9", "0": "N  0",
               "A": "↑A A", "B": "↑B B", "C": "↑C C", "D": "↑D D",
               "E": "↑E E", "F": "↑F F", "G": "↑G G", "H": "↑H H",
               "I": "↑I I", "J": "↑J J", "K": "↑K K", "L": "↑L L",
               "M": "↑M M", "N": "↑N N", "O": "↑O O", "P": "↑P P",
               "Q": "↑Q Q", "R": "↑R R", "S": "↑S S", "T": "↑T T",
               "U": "↑U U", "V": "↑V V", "W": "↑W W", "X": "↑X X",
               "Y": "↑Y Y", "Z": "↑Z Z",
               "-": "   -", "_": "   _", "[": "LB [", "]": "RB ]",
               "!": "↑1 !", "@": "↑2 @", "#": "↑3 #", "$": "↑4 $",
               "%": "↑5 %", "^": "↑6 ^", "&": "↑7 &", "*": "↑8 *",
               "(": "↑9 (", ")": "↑0 )", ",": "COM,", ".": "DOT.",
               "?": "QUE?", "/": "SL /", "\\": "BSL\\", "~": "TLD~",
               "=": "EQL=", "+": "PLS+", "{": "LC {", "}": "RC }",
               "|": "PIP|", "<": "LT <", ">": "GT >", ":": "COL:", ";": "SCL;",
               "'": "SQ '", '"': 'DQ "', " ": "SPC ",
               }

    result = subprocess.run(['nmcli', '-t', '-f', 'SSID', 'device', 'wifi', 'list'], capture_output=True, text=True)
    wifi = sorted(set([line for line in result.stdout.splitlines() if line]))
    index = 0
    while True:
        disp.display(f"{wifi[index]}")
        while input_queue.empty():
            time.sleep(0.1)
        input = input_queue.get()
        if input == 'up' or input == 'right':
            index = (index + 1) % len(wifi)
        elif input == 'down' or input == 'left':
            index = (index - 1) % len(wifi)
        elif input == '\n':
            ssid = wifi[index]
            break
    
    password = []
    index = 0
    disp.display("ENTER PASSWORD")
    time.sleep(2)
    done = False

    while True:
        if index + 1 > len(password):
            letter = 0
            password.append(letter)
        else:
            # Choose index of current 
            letter = password[index]
        if not done:
            disp.display(f"{list(symbols.values())[letter]}")
        while input_queue.empty():
            time.sleep(0.1)
        input = input_queue.get()
        if input == 'up':
            password[index] = (letter + 1) % len(symbols)
            done = False
        elif input == 'down':
            password[index] = (letter - 1) % len(symbols)
            done = False
        elif input == 'right':
            index = min(index + 1, len(password))
            done = False
        elif input == 'left':
            index = max(0, index - 1)
            done = False
        elif input == '\n':
            if done:
                break    
            pw = ""
            for i in range(len(password)):
                pw += list(symbols.keys())[password[i]]
            disp.display(f"{pw}")
            done = True
    # Configure WiFi using ssid and pw
    # This will create a persistent connection profile
    try:
        add_result = subprocess.run([
            'nmcli', 'connection', 'add',
            'type', 'wifi',
            'ifname', 'wlan0',
            'con-name', ssid,
            'ssid', ssid,
            '--',
            'wifi-sec.key-mgmt', 'wpa-psk',
            'wifi-sec.psk', pw
        ], capture_output=True, text=True)
    except Exception as e:
        # nmcli not available or other execution error
        disp.display("FAIL")
        time.sleep(2)
        return

    if add_result.returncode != 0:
        # Failed to add connection profile
        disp.display("FAIL")
        time.sleep(2)
        return

    # Activate the connection and show status
    try:
        up_result = subprocess.run(['nmcli', 'connection', 'up', ssid], capture_output=True, text=True)
    except Exception:
        disp.display("FAIL")
        time.sleep(2)
        return

    if up_result.returncode == 0:
        disp.display("GOOD")
    else:
        disp.display("FAIL")
    # Give the user a moment to see the result
    time.sleep(2)

def get_wlan0_ip():
    result = subprocess.run(['ip', 'addr', 'show', 'wlan0'], capture_output=True, text=True)
    match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', result.stdout)
    if match:
        return match.group(1)
    return None

def display_ip_address(disp, settings, input_queue):
    ip = get_wlan0_ip() or "127.0.0.1"
    octet = ip.split(".")
    index = 0
    while True:
        disp.display(f"{octet[index]}")
        while input_queue.empty():
            time.sleep(0.1)
        input = input_queue.get()
        if input == 'up' or input == 'right':
            index = min(index + 1, len(octet) - 1)
        elif input == 'down' or input == 'left':
            index = max(0, index - 1)
        elif input == '\n':
            break

def poll_encoder(q):
    """
    Install gpiozero callbacks for the rotary encoder and buttons and
    post events into queue `q`.

    This uses event-driven callbacks (no active polling) for responsive
    detection. If `PIN_A`, etc. are defined in the global namespace they
    will be used, otherwise sensible defaults are chosen.
    """
    # Resolve pin numbers from globals if present (allows compatibility
    # with how pins are defined in __main__ later in this file)
    pin_a = globals().get('PIN_A', 17)
    pin_b = globals().get('PIN_B', 18)
    pin_sw = globals().get('PIN_SW', 4)
    pin_sw_up = globals().get('PIN_SW_UP', 27)
    pin_sw_down = globals().get('PIN_SW_DOWN', 23)
    pin_sw_left = globals().get('PIN_SW_LEFT', 22)
    pin_sw_right = globals().get('PIN_SW_RIGHT', 24)

    rotor = None
    btn = None
    btn_up = None
    btn_down = None
    btn_left = None
    btn_right = None
    created_factory = False

    try:
        # Ensure there's a pin factory (the main program usually sets this,
        # but be defensive here so poll_encoder can be used standalone).
        if Device.pin_factory is None:
            Device.pin_factory = PiGPIOFactory()
            created_factory = True

        # Create the rotary encoder and use the callback API for responsiveness
        # Use max_steps=0 to allow unbounded increasing/decreasing value
        rotor = RotaryEncoder(pin_a, pin_b, wrap=True)
        last = [float(rotor.value)]        

        def _on_rotated():
            try:
                current = rotor.value
                if current != last[0]:
                    q.put('up' if current > last[0] else 'down')
                    last[0] = current
            except Exception:
                # Swallow exceptions inside the callback to avoid crashing
                # the gpiozero internal thread.
                pass

        rotor.when_rotated = _on_rotated

        # Helper to create buttons that push values into the queue
        def make_btn(pin, val):
            if pin is None:
                return None
            b = Button(pin, pull_up=True, bounce_time=0.01)
            b.when_pressed = lambda: q.put(val)
            return b

        btn = make_btn(pin_sw, '\n')
        btn_up = make_btn(pin_sw_up, 'up')
        btn_down = make_btn(pin_sw_down, 'down')
        btn_left = make_btn(pin_sw_left, 'left')
        btn_right = make_btn(pin_sw_right, 'right')

        # Block the thread while callbacks do the work. Use a short sleep so
        # the thread can be responsive to interrupts but we don't busy-loop.
        while True:
            time.sleep(1)

    except Exception as e:
        # Log error so main program can continue (or fallback to keyboard)
        print(f"Encoder polling failed: {e}")
        print(f"Error type: {type(e).__name__}")
    finally:
        # Clean up devices
        try:
            if rotor:
                rotor.close()
        except Exception:
            pass
        for b in (btn, btn_up, btn_down, btn_left, btn_right):
            try:
                if b:
                    b.close()
            except Exception:
                pass
        # Only close the pin factory if we created it here
        try:
            if created_factory and Device.pin_factory:
                Device.pin_factory.close()
                Device.pin_factory = None
        except Exception:
            pass

if __name__ == "__main__":
    # Handle Input
    input_queue = queue.Queue()
    encoder_thread = threading.Thread(target=poll_encoder, args=(input_queue,), daemon=True)
    encoder_thread.start()

    #fd = sys.stdin.fileno()
    #old_settings = termios.tcgetattr(fd)
    #tty.setcbreak(fd)
    #kb_thread = threading.Thread(target=keyboard_thread_func, args=(input_queue,), daemon=True)
    #kb_thread.start()

    # Load settings
    settings = ClockSettings()
    disp = Display(bus=1, address=0x70, brightness=settings.brightness, scroll_delay=0.5)
    state = "clock"
    menu_items = ["FLASH", "24H", "ZONE", "WIFI", "IP"]
    save_time = 0

    try:
        while True:
            if state == "clock":
                disp.colon = settings.flash_colon and (int(time.time()) % 2 == 0)
                display_time(disp, settings)
                while not input_queue.empty():
                    input = input_queue.get()
                    if input == '+' or input == 'up':
                        settings.increase_brightness()
                        disp.set_brightness(settings.brightness)
                        save_time = 10
                    elif input == '-' or input == 'down':
                        settings.decrease_brightness()
                        disp.set_brightness(settings.brightness)
                        save_time = 10
                    elif input == '\n':
                        if settings.brightness > 0:
                            settings.brightness = 0
                        else:
                            settings.brightness = 15
                        disp.set_brightness(settings.brightness)
                        save_time = 10
                    elif input == 'right' or input == 'left':
                        state = "menu"
            
            if state == "menu":
                disp.colon = False
                item = 0
                while True:
                    disp.display(menu_items[item])
                    while input_queue.empty():
                        time.sleep(0.1)
                    input = input_queue.get()
                    if input == 'right' or input == 'up':
                        item = (item + 1) % len(menu_items)
                    elif input == 'left' or input == 'down':
                        item = (item - 1) % len(menu_items)
                    elif input == '\n':
                        if menu_items[item] == "FLASH":
                            set_flash(disp, settings, input_queue)
                            state = "clock"
                            break
                        if menu_items[item] == "24H":
                            set_24h(disp, settings, input_queue)
                            state = "clock"
                            break                            
                        elif menu_items[item] == "ZONE":
                            set_timezone(disp, settings, input_queue)
                            state = "clock"
                            break
                        elif menu_items[item] == "WIFI":
                            set_wifi(disp, settings, input_queue)
                            state = "clock"
                            break
                        elif menu_items[item] == "IP":
                            display_ip_address(disp, settings, input_queue)
                            state = "clock"
                            break
                    else:
                        state = "clock"
                        break
                if save_time > 0:
                    save_time -= 1
                    id save_time == 0:
                        settings.save()
                now = time.time()
                sleep_time = 1 - (now % 1)
                time.sleep(sleep_time)                        
    except KeyboardInterrupt:
        print("\nExiting")
    finally:
        #termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        disp.clear()

