#!/usr/bin/env python3
"""
Combined Abbott server: Zoom reactions + direct tentacle control in one app.

Endpoints (port 5000 by default; change with --port):
    GET  /              Control panel web page (web/control.html)
    GET  /info          Arm count and emoji -> reaction mapping (used by the page)
    POST /reaction      Zoom reaction event, e.g. {"type": "heart"} -> plays an animation
    POST /arm           Set one tentacle: {"tentacle": 2, "height": 0.75}
    POST /arms          Set many tentacles: {"heights": [...]} or {"heights": {"3": 0.1}}
    POST /arms/release  Hand the arms back to the idle animation
    GET  /health        Health check

Arms are shared between the reaction animations and direct control:
  - A direct-control command puts the arms in "manual hold": the idle animation
    stops resetting them, so they stay where you put them.
  - A reaction animation always takes over the arms. When it finishes, the
    arms go back to idle (neutral 0.5) and manual hold is cleared.
  - POST /arms/release clears manual hold without playing a reaction.

All hardware access happens under one lock, so HTTP threads and the animation
loop never write to the servos at the same time.

Run with --test to use the browser visualization instead of real hardware.
"""

import argparse
import json
import os
import threading
from datetime import datetime
from queue import Empty, Queue
from time import sleep
from typing import Optional

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from arms.arm_animation import ArmAnimation
from arms.arm_controler import ArmController
from reaction_state_manager import (
    ZOOM_EMOJI_TO_REACTION_TYPE,
    ArmsReactionManager,
    ReactionStateManager,
)

PORT = 5000
CONTROL_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "control.html")
NEUTRAL_HEIGHT = 0.5


class ManualHoldIdleArmAnimation(ArmAnimation):
    """
    Idle animation for the arms that respects manual hold: while a
    direct-control client has positioned the arms, it leaves them alone.
    """

    def __init__(self):
        self.manual_hold = False

    def reset(self):
        pass

    def length(self) -> Optional[int]:
        return None

    def display_frame(self, arms: list[ArmController], frame_number: int):
        if self.manual_hold:
            return
        for arm in arms:
            arm.set_pos(NEUTRAL_HEIGHT)


class Abbott:
    """Owns the hardware, the reaction loop, and the lock that guards them."""

    def __init__(self, test_mode: bool):
        if test_mode:
            from graphical_stubs import make_graphical_arm_controllers
            from reaction_state_manager import make_graphical_eyes_reaction_manager

            self.arms = make_graphical_arm_controllers()
            eyes_manager = make_graphical_eyes_reaction_manager()
        else:
            from arms.arm_controler import make_arm_controllers
            from reaction_state_manager import make_eyes_reaction_manager

            self.arms = make_arm_controllers()
            eyes_manager = make_eyes_reaction_manager()

        arms_manager = ArmsReactionManager(self.arms)
        self.idle_arms = ManualHoldIdleArmAnimation()
        arms_manager.idle_animation = self.idle_arms
        arms_manager.active_animation = self.idle_arms

        self.lock = threading.Lock()
        self.reactions = ReactionStateManager([eyes_manager, arms_manager])
        self.incoming_emojis: Queue = Queue()

    @property
    def n_arms(self) -> int:
        return len(self.arms)

    def set_heights(self, positions: dict[int, float]):
        """Move tentacles directly and enter manual hold."""
        with self.lock:
            self.idle_arms.manual_hold = True
            for channel, height in positions.items():
                self.arms[channel].set_pos(height)

    def release(self):
        """Leave manual hold; the idle animation resumes on the next frame."""
        with self.lock:
            self.idle_arms.manual_hold = False

    def run_forever(self):
        """Animation loop: drain incoming reactions and push frames."""
        while True:
            with self.lock:
                try:
                    while True:
                        emoji = self.incoming_emojis.get_nowait()
                        reaction = ZOOM_EMOJI_TO_REACTION_TYPE.get(emoji)
                        if reaction is None:
                            print(f"{emoji=} not mapped to a reaction, ignoring")
                            continue
                        print(f"Queueing {reaction=} for {emoji=}")
                        # A reaction takes over the arms; afterwards they idle.
                        self.idle_arms.manual_hold = False
                        self.reactions.queue_reaction(reaction)
                except Empty:
                    pass

                self.reactions.poll()
            sleep(0.001)


ABBOTT: Optional[Abbott] = None

app = Flask(__name__)
CORS(app, origins="*")


def _now():
    return datetime.now().isoformat()


def _error(message: str, status: int):
    return jsonify({"status": "error", "message": message}), status


def _log_body(data):
    print(f"\n{'='*80}\n[{_now()}] {request.method} {request.path}")
    print(json.dumps(data, indent=2))
    print(f"{'='*80}\n")


def _get_json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    _log_body(data)
    return data


def _parse_tentacle(key, n_arms: int) -> int:
    if isinstance(key, bool) or isinstance(key, float):
        raise ValueError(f"tentacle {key!r} is not an integer")
    try:
        channel = int(key)
    except (TypeError, ValueError):
        raise ValueError(f"tentacle {key!r} is not an integer")
    if not 0 <= channel < n_arms:
        raise ValueError(f"tentacle {channel} out of range 0-{n_arms - 1}")
    return channel


def _parse_height(value, channel: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"height for tentacle {channel} must be a number")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"height {value} for tentacle {channel} outside 0.0-1.0")
    return float(value)


def _parse_heights(heights, n_arms: int) -> dict[int, float]:
    """
    Turn the "heights" field into {channel: height}, or raise ValueError.
    Accepts a list (index = tentacle; null entries skipped) or an object
    mapping tentacle numbers to heights.
    """
    if isinstance(heights, list):
        items = [(i, h) for i, h in enumerate(heights) if h is not None]
    elif isinstance(heights, dict):
        items = list(heights.items())
    else:
        raise ValueError('"heights" must be a list or an object')

    parsed = {}
    for key, value in items:
        channel = _parse_tentacle(key, n_arms)
        parsed[channel] = _parse_height(value, channel)
    return parsed


def _apply(positions: dict[int, float]):
    try:
        ABBOTT.set_heights(positions)
    except Exception as e:
        print(f"ERROR: {e}")
        return _error(str(e), 500)
    return (
        jsonify(
            {
                "status": "success",
                "timestamp": _now(),
                "set": {str(c): h for c, h in positions.items()},
            }
        ),
        200,
    )


@app.route("/reaction", methods=["POST"])
def receive_reaction():
    data = _get_json_body()
    if data is None or not isinstance(data.get("type"), str):
        return _error(
            'Body must be JSON like {"type": "heart"} '
            "with Content-Type: application/json",
            400,
        )

    ABBOTT.incoming_emojis.put_nowait(data["type"])
    return (
        jsonify(
            {
                "status": "success",
                "message": "Reaction received",
                "timestamp": _now(),
                "recognized": data["type"] in ZOOM_EMOJI_TO_REACTION_TYPE,
            }
        ),
        200,
    )


@app.route("/arm", methods=["POST"])
def set_one_arm():
    data = _get_json_body()
    if data is None or "tentacle" not in data or "height" not in data:
        return _error(
            'Body must be JSON like {"tentacle": 2, "height": 0.75} '
            "with Content-Type: application/json",
            400,
        )
    try:
        channel = _parse_tentacle(data["tentacle"], ABBOTT.n_arms)
        height = _parse_height(data["height"], channel)
    except ValueError as e:
        return _error(str(e), 400)
    return _apply({channel: height})


@app.route("/arms", methods=["POST"])
def set_many_arms():
    data = _get_json_body()
    if data is None or "heights" not in data:
        return _error(
            'Body must be JSON like {"heights": [...]} '
            "with Content-Type: application/json",
            400,
        )
    try:
        positions = _parse_heights(data["heights"], ABBOTT.n_arms)
    except ValueError as e:
        return _error(str(e), 400)
    return _apply(positions)


@app.route("/arms/release", methods=["POST"])
def release_arms():
    ABBOTT.release()
    return jsonify({"status": "success", "timestamp": _now()}), 200


@app.route("/", methods=["GET"])
def control_page():
    """Browser control panel: reaction buttons, tentacle sliders, API docs."""
    return send_file(CONTROL_PAGE)


@app.route("/info", methods=["GET"])
def info():
    """What the control page needs to render: arm count and emoji mapping."""
    emojis_by_reaction: dict[str, list[str]] = {}
    for emoji, reaction in ZOOM_EMOJI_TO_REACTION_TYPE.items():
        emojis_by_reaction.setdefault(reaction, []).append(emoji)
    return jsonify(
        {
            "n_arms": ABBOTT.n_arms,
            "neutral_height": NEUTRAL_HEIGHT,
            "reactions": emojis_by_reaction,
        }
    )


@app.route("/health", methods=["GET"])
def health_check():
    return (
        jsonify(
            {
                "status": "healthy",
                "service": "abbott-combined",
                "timestamp": _now(),
                "manual_hold": ABBOTT.idle_arms.manual_hold if ABBOTT else None,
            }
        ),
        200,
    )


def main():
    global ABBOTT

    parser = argparse.ArgumentParser(
        description="Abbott the Octopus - Zoom reactions + direct tentacle control"
    )
    parser.add_argument(
        "--test", action="store_true", help="Run with the browser visualization"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=PORT,
        help=f"HTTP port (default {PORT}; on macOS, 5000 is taken by AirPlay Receiver)",
    )
    args = parser.parse_args()
    port = args.port

    ABBOTT = Abbott(test_mode=args.test)

    print("\n" + "=" * 80)
    print(f"Abbott combined server ({'TEST' if args.test else 'HARDWARE'} mode)")
    print("=" * 80)
    print(f"     http://localhost:{port}/              control panel (open in a browser)")
    print(f"POST http://localhost:{port}/reaction      Zoom reaction")
    print(f"POST http://localhost:{port}/arm           one tentacle")
    print(f"POST http://localhost:{port}/arms          many tentacles")
    print(f"POST http://localhost:{port}/arms/release  back to idle")
    print(f"GET  http://localhost:{port}/health")
    print("=" * 80 + "\n")

    # Flask in a background thread; the animation loop owns the main thread.
    # (No debug/reloader: the reloader would start a second copy of the hardware loop.)
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, threaded=True, debug=False),
        daemon=True,
    ).start()

    try:
        ABBOTT.run_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
