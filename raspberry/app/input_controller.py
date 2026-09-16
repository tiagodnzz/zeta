from __future__ import annotations

import threading
import time
import os
from dataclasses import dataclass


@dataclass
class InputResult:
    ok: bool
    message: str


class VirtualInput:
    KEY_NAMES = {
        "ESC": "KEY_ESC", "ENTER": "KEY_ENTER", "BACKSPACE": "KEY_BACKSPACE",
        "TAB": "KEY_TAB", "SPACE": "KEY_SPACE", "SHIFT": "KEY_LEFTSHIFT",
        "CTRL": "KEY_LEFTCTRL", "ALT": "KEY_LEFTALT", "UP": "KEY_UP",
        "DOWN": "KEY_DOWN", "LEFT": "KEY_LEFT", "RIGHT": "KEY_RIGHT",
        "HOME": "KEY_HOME", "END": "KEY_END", "PAGEUP": "KEY_PAGEUP",
        "PAGEDOWN": "KEY_PAGEDOWN", "DELETE": "KEY_DELETE",
    }

    def __init__(self):
        from evdev import UInput, ecodes

        self._ecodes = ecodes
        keyboard = {ecodes.EV_KEY: [getattr(ecodes, name) for name in self.KEY_NAMES.values()]}
        keyboard[ecodes.EV_KEY] += [getattr(ecodes, f"KEY_{letter}") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
        keyboard[ecodes.EV_KEY] += [getattr(ecodes, f"KEY_{number}") for number in "0123456789"]
        keyboard[ecodes.EV_KEY] += [
            ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE,
        ]
        keyboard[ecodes.EV_REL] = [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL]
        self._ui = UInput(keyboard, name="Zeta Phone Control")
        self._lock = threading.RLock()

    def close(self):
        self._ui.close()

    def mouse(self, action, x=0, y=0):
        with self._lock:
            if action == "move_relative":
                self._ui.write(self._ecodes.EV_REL, self._ecodes.REL_X, int(x))
                self._ui.write(self._ecodes.EV_REL, self._ecodes.REL_Y, int(y))
                self._ui.syn()
            elif action in {"left", "right", "middle"}:
                button = getattr(self._ecodes, f"BTN_{action.upper()}")
                self._ui.write(self._ecodes.EV_KEY, button, 1)
                self._ui.syn()
                time.sleep(0.05)
                self._ui.write(self._ecodes.EV_KEY, button, 0)
                self._ui.syn()
                time.sleep(0.05)
            elif action in {"left_down", "left_up"}:
                self._ui.write(
                    self._ecodes.EV_KEY,
                    self._ecodes.BTN_LEFT,
                    1 if action == "left_down" else 0,
                )
                self._ui.syn()
            elif action == "double":
                self.mouse("left")
                self.mouse("left")
            else:
                return InputResult(False, "Acao de mouse invalida.")
        return InputResult(True, "ok")

    def key(self, key, pressed=False):
        normalized = str(key).upper()
        if len(normalized) == 1 and normalized.isalpha():
            code = getattr(self._ecodes, f"KEY_{normalized}", None)
        elif len(normalized) == 1 and normalized.isdigit():
            code = getattr(self._ecodes, f"KEY_{normalized}", None)
        else:
            code = getattr(self._ecodes, self.KEY_NAMES.get(normalized, ""), None)
        if code is None:
            return InputResult(False, "Tecla invalida.")
        with self._lock:
            self._ui.write(self._ecodes.EV_KEY, code, 1 if pressed else 0)
            self._ui.syn()
        return InputResult(True, "ok")


def create_virtual_input():
    try:
        return VirtualInput()
    except OSError as error:
        device = "/dev/uinput"
        try:
            device_stat = os.stat(device)
            permissions = oct(device_stat.st_mode & 0o777)
            owner = device_stat.st_uid
            group = device_stat.st_gid
            details = f"{device} modo {permissions}, uid {owner}, gid {group}"
        except OSError as stat_error:
            details = f"nao foi possivel consultar {device}: {stat_error}"
        print(
            f"Aviso: nao foi possivel abrir {device} para teclado/mouse virtual ({error}). "
            f"{details}. Instale docs/99-zeta-uinput.rules e adicione o usuario ao grupo input."
        )
        return None
    except ModuleNotFoundError as error:
        print(
            f"Aviso: dependencia ausente ({error}). Instale evdev no ambiente Python do Zeta."
        )
        return None
    except Exception as error:
        if "/dev/uinput" in str(error):
            try:
                device_stat = os.stat("/dev/uinput")
                details = (
                    f"modo {oct(device_stat.st_mode & 0o777)}, "
                    f"uid {device_stat.st_uid}, gid {device_stat.st_gid}"
                )
            except OSError as stat_error:
                details = f"nao foi possivel consultar /dev/uinput: {stat_error}"
            print(
                f"Aviso: /dev/uinput sem acesso para escrita ({error}); {details}. "
                "Instale docs/99-zeta-uinput.rules e adicione o usuario ao grupo input."
            )
            return None
        print(f"Aviso: controle uinput indisponivel: {error}")
        return None