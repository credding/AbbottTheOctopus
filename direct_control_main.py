#!/usr/bin/env python3

import argparse

from arms.arm_controler import ArmController
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import json

from graphical_stubs import GraphicalStubArmController


ARM_CONTROLLERS = []

def get_arm_controllers(is_test):
    if is_test:
        return [GraphicalStubArmController(i) for i in range(0, 8)]
    else:
        import board
        import busio
        from adafruit_pca9685 import PCA9685

        i2c = board.I2C()
        pca = PCA9685(i2c)
        pca.frequency = 60
        return [
            ArmController(pca.channels[i], False) for i in range(0,8)
        ]

        return [ArmController(i, False) for i in range (0, 7)]

app = Flask(__name__)
CORS(app, origins="*")

@app.route("/arm", methods=["POST"])
def receive_reaction():
    try:
        # Get the JSON data from the request
        data = request.get_json()

        # Print timestamp and full event data
        timestamp = datetime.now().isoformat()
        print(f"\n{'='*80}")
        print(json.dumps(data, indent=2))
        print(f"{'='*80}\n")

        channel = int(data["tentacle"])
        height = float(data["height"])

        ARM_CONTROLLERS[channel].set_pos(height)

        return (
            jsonify(
                {
                    "status": "success",
                    "timestamp": timestamp,
                }
            ),
            200,
        )


    except Exception as e:
        print(f"ERROR: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _parse_heights(heights):
    """
    Turn the "heights" field into a {channel: height} dict, or raise ValueError.

    Accepts either a list (index = tentacle; null entries are skipped) or an
    object mapping tentacle numbers (as strings or ints) to heights.
    """
    if isinstance(heights, list):
        items = [(i, h) for i, h in enumerate(heights) if h is not None]
    elif isinstance(heights, dict):
        items = list(heights.items())
    else:
        raise ValueError('"heights" must be a list or an object')

    n_arms = len(ARM_CONTROLLERS)
    parsed = {}
    for key, value in items:
        try:
            channel = int(key)
        except (TypeError, ValueError):
            raise ValueError(f"tentacle {key!r} is not an integer")
        if not 0 <= channel < n_arms:
            raise ValueError(f"tentacle {channel} out of range 0-{n_arms - 1}")

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"height for tentacle {channel} must be a number")
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"height {value} for tentacle {channel} outside 0.0-1.0")

        parsed[channel] = float(value)

    return parsed


@app.route("/arms", methods=["POST"])
def set_all_arms():
    """
    Set several (or all) tentacles in one request.

    Body: {"heights": [h0, h1, ...]}  or  {"heights": {"0": h0, "3": h3}}
    Every value is validated before any servo moves, so a bad request
    changes nothing.
    """
    timestamp = datetime.now().isoformat()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "heights" not in data:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": 'Body must be JSON like {"heights": [...]} '
                    "with Content-Type: application/json",
                }
            ),
            400,
        )

    print(f"\n{'='*80}")
    print(json.dumps(data, indent=2))
    print(f"{'='*80}\n")

    try:
        positions = _parse_heights(data["heights"])
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    try:
        for channel, height in positions.items():
            ARM_CONTROLLERS[channel].set_pos(height)
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

    return (
        jsonify(
            {
                "status": "success",
                "timestamp": timestamp,
                "set": {str(c): h for c, h in positions.items()},
            }
        ),
        200,
    )


@app.route("/health", methods=["GET"])
def health_check():
    """
    Health check endpoint.
    """
    return (
        jsonify(
            {
                "status": "healthy",
                "service": "reaction-receiver",
                "timestamp": datetime.now().isoformat(),
            }
        ),
        200,
    )    

if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("Starting direct control service")
    print("=" * 80)
    print("Listening on: http://localhost:5000")
    print("Endpoint: POST http://localhost:5000/reaction")
    print("Health check: GET http://localhost:5000/health")
    print("=" * 80 + "\n")

    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true',
                       help='Run in test mode with graphical window')
    args = parser.parse_args()

    # Create reaction manager based on mode
    ARM_CONTROLLERS = get_arm_controllers(args.test)

    app.run(host="0.0.0.0", port=5000, debug=True)
