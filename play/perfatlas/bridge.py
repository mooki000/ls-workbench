#!/usr/bin/env python3
"""A small client of the game's control bridge (newline-delimited JSON over the Unix socket named by HOS_CONTROL; the game's own
docs/control.md is the contract). Standard library only. One request at a time: a reply is the line carrying the request's id and an
`ok` key; subscription events (no `ok`) are kept, bounded, and never mistaken for a reply.

The atlas needs only: status, press, stick, shot, option_get, option_set, wait, quit. It never sends memory commands.
"""
import json
import socket
import time


class BridgeError(RuntimeError):
    """the socket is gone, or a reply did not come in time (an uncertain request is not repeated by this client)"""


class Bridge:
    def __init__(self, path, timeout=10.0, connect_timeout=0.0, clock=time.monotonic, sleep=time.sleep):
        self.path = str(path)
        self.timeout = timeout
        self.clock, self.sleep = clock, sleep
        self.next_id = 1
        self.buf = b''
        self.events = []
        self.sock = None
        end = clock() + connect_timeout
        while True:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.path)
                self.sock = s
                break
            except OSError as e:
                s.close()
                if clock() >= end:
                    raise BridgeError(f'cannot connect to {self.path}: {e}')
                sleep(0.25)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _read_line(self, deadline):
        while b'\n' not in self.buf:
            left = deadline - self.clock()
            if left <= 0:
                raise BridgeError('no reply in time')
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                raise BridgeError('no reply in time')
            except OSError as e:
                raise BridgeError(f'socket error: {e}')
            if not chunk:
                raise BridgeError('the game closed the connection')
            self.buf += chunk
        line, self.buf = self.buf.split(b'\n', 1)
        return line

    def request(self, cmd, deadline_s=None, **args):
        """send {"cmd": cmd, **args}; return the reply object (it has "ok": true|false, "frame", and the command's fields).
        A reply with ok false is returned, not raised: the caller decides (a refused teleport is an answer, not a fault).
        `timeout` among the args is the game's own (seconds a wait, shot or input may take); this client waits 5 s longer for the
        reply, or `deadline_s` when given."""
        if self.sock is None:
            raise BridgeError('the bridge is closed')
        rid = self.next_id
        self.next_id += 1
        msg = {'id': rid, 'cmd': cmd, **args}
        try:
            self.sock.sendall((json.dumps(msg, separators=(',', ':')) + '\n').encode())
        except OSError as e:
            raise BridgeError(f'send failed: {e}')
        if deadline_s is None:
            deadline_s = args['timeout'] + 5 if isinstance(args.get('timeout'), (int, float)) else self.timeout
        deadline = self.clock() + deadline_s
        while True:
            line = self._read_line(deadline)
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if 'ok' not in obj:
                if len(self.events) < 1000:
                    self.events.append(obj)
                continue
            if obj.get('id') == rid:
                return obj
            # (a reply to an earlier request that timed out: skipped)

    # ---- the few commands the atlas uses, with the fields the bridge documents

    def status(self):
        return self.request('status', deadline_s=5)

    def press(self, buttons, frames=8, wait=True):
        return self.request('press', buttons=buttons, frames=frames, exclusive=True, wait=wait, timeout=10)

    def stick(self, x, y, frames=8, which='L', wait=False):
        return self.request('stick', stick=which, x=max(-1.0, min(1.0, x)), y=max(-1.0, min(1.0, y)), frames=frames, exclusive=True, wait=wait, timeout=5)

    def release(self):
        return self.request('release', deadline_s=5)

    def shot(self, path):
        return self.request('shot', path=str(path), timeout=30)

    def option_get(self, name):
        return self.request('option_get', name=name, deadline_s=5)

    def option_set(self, name, value):
        return self.request('option_set', name=name, value=value, deadline_s=10)

    def wait_event(self, event, timeout=30, **args):
        return self.request('wait', event=event, timeout=timeout, **args)

    def quit(self):
        try:
            return self.request('quit', deadline_s=5)
        except BridgeError:
            return {'ok': True, 'note': 'closed'}
