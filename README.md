# Clock (Raspberry Pi LED Clock)

A Python-powered nightstand clock for Raspberry Pi Zero 2 W with an I²C LED display and a rotary navigation encoder.  
It syncs time via NTP, supports adjustable brightness, and includes an on-device menu to configure Wi-Fi, time format, timezone, and colon flash.

## ✨ Features

- Accurate time from NTP
- 4-digit HT16K33 LED display (14-segment)
- Smooth brightness control using a rotary encoder
- On-device settings menu:
  - Wi-Fi SSID selection + password entry
  - 12/24-hour mode
  - Timezone selection
  - Colon flash toggle
- Auto-start at boot via systemd
- Clean Python structure (Display class, input handlers, state machine)

## 🧰 Hardware

- **Raspberry Pi Zero 2 W** (other Pis likely work)
- **HT16K33 LED display** (I²C; 0x70 default address)
- **Adafruit ANO Rotary Navigation Encoder Breakout (PID 6311)**  
  – 2 quadrature pins for rotation, 5 momentary buttons, separate commons
- **3D Printed Case** https://www.tinkercad.com/things/d59hjFyJgNW-clock

> Power and logic are 3.3 V. Use internal pull-ups on the Pi GPIO and tie the board’s commons to **GND**.

### Example Wiring

| Encoder Pin | Purpose                    | Pi GPIO (BCM) | Notes                              |
|-------------|----------------------------|---------------|------------------------------------|
| ENCA        | Encoder A                  | GPIO17        | Input w/ internal pull-up          |
| ENCB        | Encoder B                  | GPIO18        | Input w/ internal pull-up          |
| COMA        | Encoder common             | GND           | Tie to Pi ground                    |
| SW1         | Button 1 (up)              | GPIO27        | Input w/ internal pull-up          |
| SW2         | Button 2 (down)            | GPIO23        | Input w/ internal pull-up          |
| SW3         | Button 3 (left)            | GPIO22        | Input w/ internal pull-up          |
| SW4         | Button 4 (right)           | GPIO24        | Input w/ internal pull-up          |
| SW5         | Button 5 (center press)    | GPIO4         | Input w/ internal pull-up          |
| COMB        | Buttons common             | GND           | Tie to Pi ground                    |

**I²C display**

| Display Pin | Purpose     | Pi Header   |
|-------------|-------------|-------------|
| VCC         | 3.3 V       | 3V3         |
| GND         | Ground      | GND         |
| SDA         | I²C data    | GPIO2 (SDA) |
| SCL         | I²C clock   | GPIO3 (SCL) |

## 🖥️ Software Requirements

- Python 3.11+ (Pi OS / Debian Bookworm/Trx)
- `smbus2` (I²C)
- `gpiozero` (inputs)  
  *(or your preferred GPIO library—adjust code accordingly)*

Optional:
- `systemd` (to run at boot)
- `timedatectl` / NTP client (for time sync)

## 🚀 Quick Start

```bash
# 1) Enable I²C (one-time)
# Add this line to /boot/firmware/config.txt and reboot:
#   dtparam=i2c_arm=on
# Then verify:
sudo apt update
sudo apt install -y i2c-tools
i2cdetect -y 1          # should show 0x70 (or your display address)

# 2) Clone the repo (use your own URL)
git clone https://github.com/<your-username>/clock.git
cd clock

# 3) Python venv
python3 -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt  # if present; otherwise:
pip install smbus2 gpiozero

# 4) Run
python clock.py
```

If your display is on a different bus or address, adjust in the code (e.g., `bus=1`, `address=0x70`).

## 🧭 Usage & Controls

- **Default state:** Clock display (updates once per second)
- **Rotate encoder:** Adjust brightness in Clock state
  - Button press (center) → save setting
- **Button press (left/right):** Enter/cycle menu
  - **Wi-Fi SSID** → choose network
    - **Wi-Fi password** → enter using buttons/rotation
  - **12/24-hour** → toggle
  - **Timezone** → choose from curated list
  - **Colon flash** → toggle
  - **Display IP Address**

## 🧪 Developer Notes

- **Display class:** wraps HT16K33 I²C operations (init, brightness, text mapping, scrolling)
- **Character mapping:** 14-segment map supports digits and a subset of ASCII; custom glyphs (e.g., arrows) are added as bitmasks
- **State machine:** `clock`, `menu/ssid`, `menu/password`, `menu/12-24`, `menu/timezone`, `menu/colon`
- **Input:** encoder rotation (quadrature) + five buttons, debounced in software via `gpiozero`

## 🧷 Systemd (run at boot)

Create `/etc/systemd/system/clock.service`:

```ini
[Unit]
Description=Pi LED Clock
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/clock
ExecStart=/home/pi/clock/venv/bin/python /home/pi/clock/clock.py
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now clock.service
systemctl status clock.service
```

## 📶 Wi-Fi from the App

The menu can scan SSIDs and configure credentials on-device. If you prefer CLI:

```bash
# List SSIDs
nmcli dev wifi list

# Connect
sudo nmcli dev wifi connect "YOUR_SSID" password "YOUR_PASSWORD"
```

## 🧯 Troubleshooting

- **Display shows nothing**
  - `i2cdetect -y 1` should show `0x70` (or your address)
  - Check `dtparam=i2c_arm=on` and SDA/SCL wiring
- **Only some segments light**
  - Verify 14-segment mapping and that you’re writing both high/low bytes per digit
- **Encoder not responding**
  - Confirm you wired **COMA/COMB to GND** and enabled internal pull-ups on ENCA/ENCB/SW*
  - Try swapping A/B if direction feels reversed
- **Buttons double-trigger**
  - Add debounce in code (gpiozero has `bounce_time`), ensure solid ground
- **Permissions**
  - User must be in `i2c` and `gpio` groups on some setups:
    ```bash
    sudo adduser $USER i2c
    sudo adduser $USER gpio
    ```
    Log out/in.

## 🧾 License

GNU GENERAL PUBLIC LICENSE (GPLv3)


