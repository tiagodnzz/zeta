import asyncio
import json
import os
import subprocess
import threading
import time
import tempfile
import re
from urllib.parse import quote_plus
from glob import glob
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from .input_controller import VirtualInput


ROBOT_MODES = (
    "FOLLOW_HAND", "FOLLOW_FACE", "COUNT_FINGERS",
    "OBJECT_DETECTION", "DATE_TIME", "DRAWING", "YOUTUBE", "SPOTIFY", "BROWSER",
)
WEB_HOST = os.environ.get("ZETA_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("ZETA_WEB_PORT", "8080"))
WEB_ROOT = Path(__file__).with_name("web")


@dataclass
class WebCommand:
    kind: str
    value: str = ""


class ChatRequest(BaseModel):
    text: str


class ModeRequest(BaseModel):
    mode: str


class TerminalRequest(BaseModel):
    command: str


class MouseRequest(BaseModel):
    action: str
    x: float | None = None
    y: float | None = None


class KeyRequest(BaseModel):
    key: str
    pressed: bool = False


class ServoRequest(BaseModel):
    x: float
    y: float


WHISPER_MODEL_NAME = os.environ.get("ZETA_WHISPER_MODEL", "tiny")
WHISPER_DEVICE = os.environ.get("ZETA_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("ZETA_WHISPER_COMPUTE_TYPE", "int8")
WHISPER_CPU_THREADS = int(os.environ.get("ZETA_WHISPER_CPU_THREADS", str(os.cpu_count() or 1)))
_whisper_model = None
_whisper_model_lock = threading.Lock()


def youtube_search_query(text):
    match = re.search(
        r"\b(?:procure|pesquise|buscar|busque|pesquisar|toque|tocar)\s+(.+?)\s+"
        r"(?:no|na|em)\s+youtube\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    query = " ".join(match.group(1).split()).strip(" .,!?:;")
    return query or None


def transcribe_audio(audio, suffix):
    global _whisper_model
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError("faster-whisper nao esta instalado no ambiente do Zeta.") from error

    if _whisper_model is None:
        with _whisper_model_lock:
            if _whisper_model is None:
                _whisper_model = WhisperModel(
                    WHISPER_MODEL_NAME,
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                    cpu_threads=WHISPER_CPU_THREADS,
                )

    with tempfile.TemporaryDirectory(prefix="zeta-whisper-") as temp_dir:
        input_path = Path(temp_dir) / f"input{suffix}"
        input_path.write_bytes(audio)
        conversion = subprocess.run(
            (
                "ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(input_path),
                "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1",
            ),
            capture_output=True,
            text=False,
            check=False,
        )
        if conversion.returncode != 0:
            error = conversion.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Nao foi possivel decodificar o audio: {error}")
        try:
            import numpy as np
        except ImportError as error:
            raise RuntimeError("numpy nao esta instalado no ambiente do Zeta.") from error
        audio_samples = np.frombuffer(conversion.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = _whisper_model.transcribe(
            audio_samples,
            language="pt",
            beam_size=1,
            best_of=1,
            temperature=0,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


def read_system_metrics():
    metrics = {
        "cpu_load": 0.0,
        "cpu_cores": os.cpu_count() or 1,
        "memory_used": 0.0,
        "memory_total_mb": 0,
        "temperature": None,
    }
    try:
        load = os.getloadavg()[0]
        metrics["cpu_load"] = round(min(100.0, load / metrics["cpu_cores"] * 100), 1)
    except OSError:
        pass
    try:
        memory = {}
        with open("/proc/meminfo", encoding="ascii") as file:
            for line in file:
                key, value = line.split(":", 1)
                memory[key] = int(value.strip().split()[0])
        total = memory.get("MemTotal", 0)
        available = memory.get("MemAvailable", memory.get("MemFree", 0))
        metrics["memory_total_mb"] = round(total / 1024)
        metrics["memory_used"] = round((total - available) / total * 100, 1) if total else 0.0
    except (OSError, ValueError):
        pass
    for path in glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            metrics["temperature"] = round(int(Path(path).read_text().strip()) / 1000, 1)
            break
        except (OSError, ValueError):
            continue
    return metrics


class RobotWebState:
    def __init__(self, mode: str):
        self._lock = threading.Lock()
        self._state = {
            "mode": mode,
            "gesture": "none",
            "finger_count": 0,
            "objects": [],
            "last_message": "Pronto",
            "chat_busy": False,
            "chat_messages": [{"text": "Estou ouvindo.", "kind": "incoming"}],
            "frame": None,
            "desktop_frame": None,
            "input_ready": False,
            "metrics": read_system_metrics(),
        }

    def update(self, **values):
        with self._lock:
            self._state.update(values)

    def add_chat_message(self, text, kind):
        with self._lock:
            self._state["chat_messages"].append({"text": text, "kind": kind})
            self._state["chat_messages"] = self._state["chat_messages"][-40:]

    def snapshot(self):
        with self._lock:
            snapshot = dict(self._state)
            snapshot.pop("frame", None)
            snapshot.pop("desktop_frame", None)
            return snapshot

    def frame(self):
        with self._lock:
            return self._state["frame"]

    def desktop_frame(self):
        with self._lock:
            return self._state["desktop_frame"]


def capture_desktop_loop(state, stop_event):
    while not stop_event.is_set():
        try:
            result = subprocess.run(
                ("grim", "-c", "-t", "png", "-"),
                capture_output=True,
                timeout=3,
                check=True,
            )
            if result.stdout:
                state.update(desktop_frame=result.stdout)
        except (OSError, subprocess.SubprocessError):
            pass
        stop_event.wait(0.25)


TERMINAL_COMMANDS = {
    "pwd": ("pwd",),
    "ls": ("ls", "-la"),
    "ip": ("ip", "-brief", "address"),
    "uptime": ("uptime",),
    "df": ("df", "-h"),
    "free": ("free", "-h"),
    "date": ("date",),
}


def run_terminal_command(command):
    command = " ".join(command.split())
    if command not in TERMINAL_COMMANDS:
        return "Comando bloqueado. Permitidos: " + ", ".join(TERMINAL_COMMANDS)
    try:
        result = subprocess.run(
            TERMINAL_COMMANDS[command],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"Falha: {error}"
    output = (result.stdout or result.stderr).strip()
    return output[-4000:] or f"codigo de saida: {result.returncode}"


def create_app(command_queue, chat_queue, state: RobotWebState, stop_event, virtual_input=None):
    app = FastAPI(title="Zeta", version="0.1.0")

    @app.get("/")
    async def index():
        return FileResponse(WEB_ROOT / "index.html")

    @app.get("/app.js")
    async def javascript():
        return FileResponse(WEB_ROOT / "app.js", media_type="text/javascript")

    @app.get("/style.css")
    async def stylesheet():
        return FileResponse(WEB_ROOT / "style.css", media_type="text/css")

    @app.get("/stream.mjpg")
    async def stream():
        async def frames():
            last_frame = None
            while not stop_event.is_set():
                frame = state.frame()
                if frame is not None and frame != last_frame:
                    last_frame = frame
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                await asyncio.sleep(0.08)
        return StreamingResponse(
            frames(), media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.get("/desktop.mjpg")
    async def desktop_stream():
        async def frames():
            last_frame = None
            while not stop_event.is_set():
                frame = state.desktop_frame()
                if frame is not None and frame != last_frame:
                    last_frame = frame
                    yield b"--frame\r\nContent-Type: image/png\r\n\r\n" + frame + b"\r\n"
                await asyncio.sleep(0.25)
        return StreamingResponse(
            frames(), media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.get("/api/status")
    async def status():
        state.update(metrics=read_system_metrics())
        return state.snapshot()

    @app.post("/api/chat")
    async def chat(request: ChatRequest):
        text = " ".join(request.text.split())
        if not text:
            return {"accepted": False, "error": "Mensagem vazia."}
        youtube_query = youtube_search_query(text)
        if youtube_query:
            command_queue.put_nowait(WebCommand("youtube_search", quote_plus(youtube_query)))
            state.add_chat_message(text, "outgoing")
            return {"accepted": True, "youtube_search": youtube_query}
        try:
            chat_queue.put_nowait(text)
        except Exception:
            return {"accepted": False, "error": "Zeta ainda esta respondendo."}
        state.update(chat_busy=True, last_message="Pensando...")
        state.add_chat_message(text, "outgoing")
        return {"accepted": True}

    @app.post("/api/transcribe")
    async def transcribe(audio: UploadFile = File(...)):
        content_type = audio.content_type or ""
        suffix = ".webm" if "webm" in content_type else ".ogg"
        try:
            text = await asyncio.to_thread(transcribe_audio, await audio.read(), suffix)
        except (OSError, RuntimeError, UnicodeError) as error:
            return {"accepted": False, "error": str(error)}
        if not text:
            return {"accepted": False, "error": "Nenhuma fala detectada."}
        youtube_query = youtube_search_query(text)
        if youtube_query:
            command_queue.put_nowait(WebCommand("youtube_search", quote_plus(youtube_query)))
            state.add_chat_message(text, "outgoing")
            return {"accepted": True, "text": text, "youtube_search": youtube_query}
        try:
            chat_queue.put_nowait(text)
        except Exception:
            return {"accepted": False, "error": "Zeta ainda esta respondendo."}
        state.update(chat_busy=True, last_message="Pensando...")
        state.add_chat_message(text, "outgoing")
        return {"accepted": True, "text": text}

    @app.post("/api/mode")
    async def mode(request: ModeRequest):
        if request.mode not in ROBOT_MODES:
            return {"accepted": False, "error": "Modo invalido."}
        command_queue.put_nowait(WebCommand("mode", request.mode))
        return {"accepted": True, "mode": request.mode}

    @app.post("/api/stop")
    async def stop():
        command_queue.put_nowait(WebCommand("stop"))
        return {"accepted": True}

    @app.post("/api/servo")
    async def servo(request: ServoRequest):
        command_queue.put_nowait(WebCommand("servo", f"{request.x},{request.y}"))
        return {"accepted": True}

    @app.post("/api/terminal")
    async def terminal(request: TerminalRequest):
        return {"output": await asyncio.to_thread(run_terminal_command, request.command)}

    @app.post("/api/mouse")
    async def mouse(request: MouseRequest):
        if virtual_input is None:
            return {"accepted": False, "error": "Entrada virtual indisponivel. Verifique evdev e /dev/uinput."}
        result = await asyncio.to_thread(
            virtual_input.mouse, request.action, round(request.x or 0), round(request.y or 0)
        )
        return {"accepted": result.ok, "output": result.message}

    @app.post("/api/key")
    async def key(request: KeyRequest):
        if virtual_input is None:
            return {"accepted": False, "error": "Entrada virtual indisponivel. Verifique evdev e /dev/uinput."}
        result = await asyncio.to_thread(virtual_input.key, request.key, request.pressed)
        return {"accepted": result.ok, "output": result.message}

    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        await websocket.accept()
        try:
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                    payload = json.loads(message)
                    if payload.get("type") == "chat":
                        text = " ".join(str(payload.get("text", "")).split())
                        if text:
                            try:
                                chat_queue.put_nowait(text)
                                state.update(chat_busy=True, last_message="Pensando...")
                                state.add_chat_message(text, "outgoing")
                            except Exception:
                                pass
                    elif payload.get("type") == "mode":
                        mode_name = payload.get("mode")
                        if mode_name in ROBOT_MODES:
                            command_queue.put_nowait(WebCommand("mode", mode_name))
                    elif payload.get("type") == "stop":
                        command_queue.put_nowait(WebCommand("stop"))
                except asyncio.TimeoutError:
                    pass
                state.update(metrics=read_system_metrics())
                await websocket.send_json(state.snapshot())
        except (WebSocketDisconnect, RuntimeError):
            return

    return app


def run_web_server(command_queue, chat_queue, state, stop_event, virtual_input=None):
    import uvicorn

    threading.Thread(
        target=capture_desktop_loop,
        args=(state, stop_event),
        daemon=True,
        name="desktop-capture",
    ).start()
    app = create_app(command_queue, chat_queue, state, stop_event, virtual_input)
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="info")