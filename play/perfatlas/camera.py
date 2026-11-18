#!/usr/bin/env python3
"""Turning the game's camera to a heading with the right stick, closed loop on the bridge's own reading.

`status.game.camera` is the camera's heading in degrees (0 toward +z, 90 toward +x). The stick's handedness and rate are not known in
advance: the first push is a calibration (a short push to the right, read what it did), as hsap's `camera to` does. Link's own heading
is not touched (the longer `!pos` form's facing and camera angles did not take effect in the game, and were dropped from `!pos`).
"""
import time

from .bridge import BridgeError


def wrap(d):
    return (d + 180.0) % 360.0 - 180.0


class Aimer:
    def __init__(self, bridge, sleep=time.sleep, status=None):
        self.bridge = bridge
        self.sleep = sleep
        self.read_status = status or bridge.status   # (a caller with retries on a loaded PC passes its own)
        self.sign = None       # +1 when a push to the right (x > 0) raises the heading
        self.rate = 1.2        # degrees a frame at full push (a first guess, replaced by the calibration)

    def push(self, x, frames):
        """the right stick: x in -1..1 for `frames` presents (the reply comes when the input is over); then a short wait for the camera to
        stop easing, and a fresh status (the flattened form of the caller: {'game': {...}})"""
        self.bridge.stick(x, 0.0, frames=frames, which='R', wait=True)
        self.sleep(0.4)
        return self.read_status()

    @staticmethod
    def game(st):
        return (st.get('game') if isinstance(st, dict) else None) or {}

    def aim(self, yaw, tol=10.0, tries=8):
        """returns (camera_ok, camera heading, camera pitch, pushes)"""
        st = self.read_status()
        pushes = 0
        for _ in range(tries):
            g = self.game(st)
            cam = g.get('camera')
            if cam is None:
                return False, None, g.get('camera_pitch'), pushes
            err = wrap(yaw - cam)
            if abs(err) <= tol:
                return True, cam, g.get('camera_pitch'), pushes
            if self.sign is None:
                before = cam
                st = self.push(1.0, 12)
                pushes += 1
                after = self.game(st).get('camera')
                if after is not None and abs(wrap(after - before)) >= 1.0:
                    self.sign = 1.0 if wrap(after - before) > 0 else -1.0
                    self.rate = max(0.2, abs(wrap(after - before)) / 12.0)
                continue
            direction = 1.0 if err * self.sign > 0 else -1.0
            frames = int(max(2, min(120, round(abs(err) / self.rate * 0.85))))
            st = self.push(direction, frames)
            pushes += 1
        g = self.game(st)
        cam = g.get('camera')
        return (cam is not None and abs(wrap(yaw - cam)) <= tol), cam, g.get('camera_pitch'), pushes
