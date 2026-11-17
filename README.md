# Abbott the Octopus 🐙

> **Note:** This README was AI-generated (by Claude) from a reading of the source code. It may contain inaccuracies — please verify against the code and correct as needed.

Abbott is a physical, animatronic octopus that reacts to Zoom meeting emoji reactions. When someone in a meeting sends a 🎉, ❤️, 👍, etc., Abbott responds by animating its two round LCD "eyes" and waving its servo-driven tentacles.

It runs on a Raspberry Pi (wiring docs reference a Pi Zero 2) with:

- **Two GC9A01 240×240 round LCDs** as eyes, driven as Linux framebuffers (`/dev/fb0`, `/dev/fb1`)
- **A PCA9685 16-channel PWM board** (over I²C) driving up to 8 tentacle servos
- **A Zoom App** that listens for in-meeting reactions and forwards them to the Pi over HTTP

## How it works

```
Zoom meeting ──► Zoom App (zoomapp/frontend, React)
                   │  zoomSdk.onEmojiReaction / onReaction / onFeedbackReaction
                   ▼
                 zoomapp/backend (Node/Express)  POST /api/zoomapp/log-reaction
                   │  forwards to REACTION_RECEIVER_URL
                   ▼
                 zoom_receiver_main.py (Flask, port 5000)  POST /reaction
                   │  maps Zoom emoji name → ReactionType
                   ▼
                 ReactionStateManager ──► EyesReactionManager ──► eye animations ──► framebuffers
                                      └─► ArmsReactionManager ──► arm animations ──► PCA9685 servos
```

### Reactions

Zoom emoji names are mapped to four reaction types in `reaction_state_manager.py` (`ZOOM_EMOJI_TO_REACTION_TYPE`):

| Reaction   | Zoom emojis                    | Eyes animation      | Arms animation                         |
|------------|--------------------------------|---------------------|----------------------------------------|
| `Excited`  | `tada`, `joy`                  | `ExcitedAnimation` (eyes dart side to side) | `BigWaveAnimation` (all arms wave, alternating) |
| `Love`     | `heart`, `clap`                | `HeartAnimation` (pulsing heart)            | `RaiseArmsAnimation` (raise two arms + pumps)   |
| `Shocked`  | `rocket`, `openmouth`          | `ExcitedAnimation`                          | `ShockedArmsAnimation` (rapid small flaps)      |
| `ThumbsUp` | `thumbsup`, `balloon`          | `WinkyAnimation` (right eye winks)          | `ThumbsUpAnimation` (one arm raises and holds)  |

When nothing is happening, Abbott idles: the eyes wander randomly (`IdleEyesAnimation`) and the arms rest at mid-position (`IdleArmAnimation`).

### Animation engine

`ReactionStateManager` runs a simple frame-based loop at a nominal **60 Hz**:

- Reactions are queued (duplicates of an already-queued reaction are ignored) and played one at a time.
- Each reaction runs for the length of its longest sub-animation (eyes or arms), then the next queued reaction plays, or Abbott returns to idle.
- Frame numbers are computed from wall-clock time, so animations should be written as functions of `frame_number` (or scale their state by the number of frames elapsed) — the loop may skip frames.
- Eye frames are downsampled to every 6th frame (~10 Hz) because the displays can't refresh at 60 Hz.

To add a new animation, subclass `EyeAnimation` (`eyes/animations/animation.py`) or `ArmAnimation` (`arms/arm_animation.py`), implement `reset()`, `length()` (in frames, `None` = infinite) and `display_frame(...)`, and register it in the relevant manager's `animations` dict. Note "left/right" means Abbott's left/right (stage left/right).

## Project layout

```
commandline_main.py       Keyboard-driven control (e/h/s/t) — handy for testing
zoom_receiver_main.py     Flask server on :5000 that receives reactions from the Zoom App backend
direct_control_main.py    Flask server on :5000 to set individual tentacle positions (POST /arm)
tentacle_control.py       Tiny web UI on :8080 with sliders that POST to direct_control_main.py
reaction_state_manager.py Reaction queue, emoji mapping, and eye/arm sub-managers
graphical_stubs.py        Fake displays/servos + browser visualization on :8765 for --test mode
arms/                     Servo controller (PCA9685) and arm animations
eyes/                     Framebuffer display driver, eye images, and eye animations
  eye_controllers_fb.py   Framebuffer-based displays (used by the main app)
  eye_controllers.py      Older direct-SPI driver via adafruit_rgb_display (used by sample scripts)
  SETUP.md                SPI setup and eye wiring tables
gc9a01-configurable.dts   Device-tree overlay for GC9A01 displays with configurable pins
gc9a01-configurable-README.md  How to compile/install/configure the overlay
setup.md                  Raspberry Pi setup notes
zoomapp/                  Zoom App (React frontend + Node backend), based on Zoom's Advanced Sample
  python-receiver/        Minimal standalone receiver that just logs reaction events
```

## Setup

### Raspberry Pi

See `setup.md`, `eyes/SETUP.md` and `gc9a01-configurable-README.md` for details. In short:

1. Compile and install the `gc9a01-configurable` overlay, then add to `/boot/firmware/config.txt`:

   ```
   dtoverlay=spi1-1cs
   dtoverlay=gc9a01-configurable,dc_gpio=25,reset_gpio=27,led_gpio=255,speed=40000000
   dtoverlay=gc9a01-configurable,spi=1,dc_gpio=26,reset_gpio=6,led_gpio=255,speed=40000000
   ```

   This exposes the left eye as `/dev/fb0` and the right eye as `/dev/fb1`. (If an HDMI monitor is attached and `/dev/fb2` exists, the code shifts to `fb1`/`fb2`.)

2. Enable I²C for the PCA9685 servo board.

3. Install system libs and Python deps:

   ```bash
   sudo apt install libopenjp2-7 libopenblas0
   python -m venv env
   source env/bin/activate
   pip install -r requirements.txt
   ```

   (A `pyproject.toml` / `uv.lock` is also present for use with `uv`.)

Servo pulse range is set by `MIN_DUTY` / `MAX_DUTY` in `arms/arm_controler.py`.

### Zoom App

See `zoomapp/README.md` for creating the Zoom Marketplace app, configuring ngrok, and running the frontend/backend with Docker. Set `REACTION_RECEIVER_URL` in `zoomapp/.env` to point at the Pi, e.g. `http://<pi-address>:5000/reaction`.

## Running

Run all commands from the repository root (image paths like `eyes/Eye.png` are relative to it).

**React to Zoom reactions (on the Pi):**

```bash
python zoom_receiver_main.py
```

**Keyboard control:**

```bash
python commandline_main.py          # on the Pi, with hardware
python commandline_main.py --test   # on a laptop, no hardware
```

Type `e` (Excited), `h` (Love), `s` (Shocked) or `t` (ThumbsUp) and press Enter.

In `--test` mode, the eyes and arms are replaced by stubs from `graphical_stubs.py` and a live visualization is served at <http://localhost:8765>.

**Manual tentacle control (for calibrating servos):**

```bash
python direct_control_main.py       # add --test to run without hardware
python tentacle_control.py          # then open http://localhost:8080
```

Note: the IP address that `tentacle_control.py` posts to is hardcoded in its HTML; edit it to match your Pi.

## Direct control API (`direct_control_main.py`)

`direct_control_main.py` is a small Flask server that lets any HTTP client set the position of a single tentacle servo. It does not use the reaction/animation system: each request moves one servo immediately, and it stays there until another request moves it.

- **Host/port:** `0.0.0.0:5000` (reachable from other machines on the network)
- **CORS:** allowed from any origin, so browser pages served from elsewhere (like `tentacle_control.py`) can call it
- **Startup banner:** the banner says `POST /reaction`, but the real endpoint is **`POST /arm`**

### `POST /arm`: set one tentacle's position

**Required headers**

| Header         | Value              |
|----------------|--------------------|
| `Content-Type` | `application/json` |

The header is required. Without it, Flask rejects the body and the server returns a 500 error (see below).

**Body:** a JSON object with exactly these two fields:

| Field      | Type                          | Meaning |
|------------|-------------------------------|---------|
| `tentacle` | integer (or numeric string)   | Which servo to move. Passed through `int()`, so `3` and `"3"` both work. `3.9` (a JSON number) is truncated to `3`, but the string `"3.9"` is an error. |
| `height`   | number (or numeric string)    | Target position. Passed through `float()`. `0.0` is fully down, `1.0` is fully up, and `0.5` is neutral (the idle position the animations use). |

Extra fields are ignored. Field names are case-sensitive.

**Valid `tentacle` values**

| Mode                  | Valid indices | Notes |
|-----------------------|---------------|-------|
| Hardware (default)    | `0`–`7`       | Index = PCA9685 channel number. All 8 arms are created **non-inverted** here, which differs from the animation code (it inverts every arm). So the same `height` can move a servo in the opposite direction from an animation. |
| `--test`              | `0`–`6`       | Only 7 stub controllers are created, so `7` returns an error. All stubs are built with index `True` (i.e. `1`), so every request currently moves **arm 1** in the visualization at <http://localhost:8765>, whatever `tentacle` you send. |

Negative indices follow Python list indexing (`-1` is the last arm). Avoid them.

**How `height` maps to the servo:** in `arms/arm_controler.py`:

```
duty_cycle = MIN_DUTY + round(height * (MAX_DUTY - MIN_DUTY))
           = 0x0600   + round(height * 0x2200)
```

`MIN_DUTY` (`0x0600`) and `MAX_DUTY` (`0x2800`) are the range where the servo was found to actually move, at a PWM frequency of 60 Hz. **`height` is not clamped.** Values outside `0.0`–`1.0` push the servo past its tested range and can strain it. Values far enough outside that the duty cycle leaves `0x0000`–`0xFFFF` (below about `-0.18` or above about `7.3`) make the PCA9685 driver raise an error.

**Example request**

```http
POST /arm HTTP/1.1
Host: <pi-address>:5000
Content-Type: application/json

{"tentacle": 2, "height": 0.75}
```

```bash
curl -X POST http://<pi-address>:5000/arm \
  -H 'Content-Type: application/json' \
  -d '{"tentacle": 2, "height": 0.75}'
```

```python
import requests
requests.post("http://<pi-address>:5000/arm", json={"tentacle": 2, "height": 0.75})
```

```js
fetch("http://<pi-address>:5000/arm", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ tentacle: 2, height: 0.75 }),
});
```

**Success response:** `200 OK`

```json
{"status": "success", "timestamp": "2026-11-17T14:03:12.345678"}
```

`timestamp` is the Pi's local time when the request was handled (ISO 8601, no timezone).

**Error response:** `500 Internal Server Error`

Every failure returns a 500 with the exception text in `message`, including problems that are really client errors:

```json
{"status": "error", "message": "'height'"}
```

| Cause                                   | Example `message` |
|-----------------------------------------|-------------------|
| Missing field                           | `'tentacle'` or `'height'` (the missing key name) |
| Non-numeric value                       | `invalid literal for int() with base 10: 'left'` / `could not convert string to float: 'up'` |
| `tentacle` out of range                 | `list index out of range` |
| Missing/wrong `Content-Type`            | `415 Unsupported Media Type: ...` |
| Malformed JSON                          | `400 Bad Request: ...` |
| `height` far out of range (hardware)    | an error from the PCA9685 driver about the duty cycle |

The server also prints every request body it receives to the console, which helps with debugging.

**Moving several tentacles:** send one request per tentacle. There is no batch form and no interpolation: each servo moves to the new position as fast as it can. For smooth motion, send a stream of small steps (the slider in `tentacle_control.py` does this, posting on every `input` event).

### `GET /health`

Returns `200 OK`. It does not touch the hardware.

```json
{"status": "healthy", "service": "reaction-receiver", "timestamp": "2026-11-17T14:03:12.345678"}
```

(`service` says `reaction-receiver` because the code was copied from the reaction receiver.)

### Running it

```bash
python direct_control_main.py          # real servos via PCA9685 over I²C
python direct_control_main.py --test   # stub arms + browser visualization on :8765
```

It runs with Flask `debug=True`, so it auto-reloads when code changes. Do not run it at the same time as `zoom_receiver_main.py`: both use port 5000 and the same servos.

## Known rough edges

- `main.py` is a placeholder.
- All arms are currently set to inverted (`TODO - work out invertions` in `arms/arm_controler.py`).
- `Shocked` reuses the `Excited` eye animation; several Zoom emojis share animations.
- `zoom_receiver_main.py` uses the hardware managers directly and has no `--test` mode.
