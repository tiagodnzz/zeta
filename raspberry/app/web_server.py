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

from . import config
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from .input_controller import VirtualInput


ROBOT_MODES = (
    "FOLLOW_HAND", "FOLLOW_FACE", "COUNT_FINGERS",
    "OBJECT_DETECTION", "DATE_TIME", "DRAWING", "YOUTUBE", "SPOTIFY", "BROWSER", "SYSTEM",
)
WEB_HOST = os.environ.get("ZETA_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("ZETA_WEB_PORT", "8080"))
WEB_ROOT = Path(__file__).with_name("web")
CONFIG_KEYS = {
    "tts_enabled": ("ZETA_TTS_ENABLED", lambda value: "1" if value else "0"),
    "ai_provider": ("ZETA_AI_PROVIDER", lambda value: value),
    "transcription_provider": ("ZETA_TRANSCRIPTION_PROVIDER", lambda value: value),
    "menu_navigation": ("ZETA_MENU_NAVIGATION", lambda value: value),
    "menu_dwell_time_s": ("ZETA_MENU_DWELL_TIME_S", lambda value: f"{value:.1f}"),
}


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


class SettingsRequest(BaseModel):
    tts_enabled: bool = True
    ai_provider: str = "ollama"
    transcription_provider: str = "local"
    menu_navigation: str = "gestures"
    menu_dwell_time_s: float = 1.5


WHISPER_MODEL_NAME = os.environ.get("ZETA_WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("ZETA_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("ZETA_WHISPER_COMPUTE_TYPE", "int8")
WHISPER_CPU_THREADS = int(os.environ.get("ZETA_WHISPER_CPU_THREADS", str(os.cpu_count() or 1)))
WHISPER_BEAM_SIZE = int(os.environ.get("ZETA_WHISPER_BEAM_SIZE", "5"))
WHISPER_INITIAL_PROMPT = os.environ.get(
    "ZETA_WHISPER_INITIAL_PROMPT",
    "Comandos em portugues do Brasil para controlar o robo e pesquisar no YouTube.",
)
TRANSCRIPTION_PROVIDER = os.environ.get("ZETA_TRANSCRIPTION_PROVIDER", "local").strip().lower()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_TRANSCRIPTION_MODEL = os.environ.get(
    "ZETA_GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo"
)
GROQ_TRANSCRIPTION_TEMPERATURE = float(
    os.environ.get("ZETA_GROQ_TRANSCRIPTION_TEMPERATURE", "0")
)
_whisper_model = None
_whisper_model_lock = threading.Lock()
_groq_client = None
_groq_client_lock = threading.Lock()


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


def transcribe_audio_groq(audio, suffix):
    global _groq_client
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY nao foi configurada.")
    try:
        from groq import Groq
    except ImportError as error:
        raise RuntimeError("A biblioteca groq nao esta instalada no ambiente do Zeta.") from error

    if _groq_client is None:
        with _groq_client_lock:
            if _groq_client is None:
                _groq_client = Groq(api_key=GROQ_API_KEY)
    transcription = _groq_client.audio.transcriptions.create(
        file=(f"voice{suffix}", audio),
        model=GROQ_TRANSCRIPTION_MODEL,
        language="pt",
        temperature=GROQ_TRANSCRIPTION_TEMPERATURE,
        response_format="verbose_json",
    )
    text = getattr(transcription, "text", "")
    return text.strip()


def transcribe_audio_local(audio, suffix):
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
                "-vn", "-af", "highpass=f=80,lowpass=f=7600,loudnorm=I=-16:LRA=11:TP=-1.5",
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
            beam_size=WHISPER_BEAM_SIZE,
            best_of=WHISPER_BEAM_SIZE,
            temperature=0,
            initial_prompt=WHISPER_INITIAL_PROMPT,
            no_speech_threshold=0.4,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 250,
                "min_speech_duration_ms": 120,
            },
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


def transcribe_audio(audio, suffix):
    if TRANSCRIPTION_PROVIDER == "groq":
        return transcribe_audio_groq(audio, suffix)
    if TRANSCRIPTION_PROVIDER == "local":
        return transcribe_audio_local(audio, suffix)
    raise RuntimeError(
        f"Provedor de transcricao invalido: {TRANSCRIPTION_PROVIDER}. Use groq ou local."
    )


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


def read_public_settings():
    return {
        "tts_enabled": os.environ.get("ZETA_TTS_ENABLED", "1") != "0",
        "ai_provider": os.environ.get("ZETA_AI_PROVIDER", "groq" if os.environ.get("GROQ_API_KEY") else "ollama"),
        "transcription_provider": os.environ.get("ZETA_TRANSCRIPTION_PROVIDER", "local"),
        "menu_navigation": os.environ.get("ZETA_MENU_NAVIGATION", "gestures"),
        "menu_dwell_time_s": float(os.environ.get("ZETA_MENU_DWELL_TIME_S", "1.5")),
    }


def save_public_settings(settings):
    values = {
        "tts_enabled": settings.tts_enabled,
        "ai_provider": settings.ai_provider.strip().lower(),
        "transcription_provider": settings.transcription_provider.strip().lower(),
        "menu_navigation": settings.menu_navigation.strip().lower(),
        "menu_dwell_time_s": round(float(settings.menu_dwell_time_s), 1),
    }
    if values["ai_provider"] not in {"groq", "ollama"}:
        raise ValueError("Provedor de conversa invalido.")
    if values["transcription_provider"] not in {"groq", "local"}:
        raise ValueError("Provedor de transcricao invalido.")
    if values["menu_navigation"] not in {"gestures", "pointer"}:
        raise ValueError("Modo de navegacao invalido.")
    if not 0.5 <= values["menu_dwell_time_s"] <= 5.0:
        raise ValueError("O tempo de selecao deve estar entre 0,5 e 5 segundos.")
    env_path = config.ENV_FILE
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    replacements = {key: formatter(values[name]) for name, (key, formatter) in CONFIG_KEYS.items()}
    found = set()
    updated_lines = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in replacements:
            updated_lines.append(f"{key}={replacements[key]}")
            found.add(key)
        else:
            updated_lines.append(line)
    for key, value in replacements.items():
        if key not in found:
            updated_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")
    return values


class RobotWebState:
    def __init__(self, mode: str):
        self._lock = threading.Lock()
        self._state = {
            "mode": mode,
            "gesture": "none",
            "finger_count": 0,
            "mic_listening": False,
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


def create_app(
    command_queue,
    chat_queue,
    state: RobotWebState,
    stop_event,
    virtual_input=None,
    inference_active=None,
):
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

    @app.get("/api/settings")
    async def settings():
        return read_public_settings()

    @app.post("/api/settings")
    async def update_settings(request: SettingsRequest):
        try:
            values = save_public_settings(request)
        except (OSError, ValueError) as error:
            return {"accepted": False, "error": str(error)}
        return {"accepted": True, "settings": values, "restart_required": True}

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
        audio_bytes = await audio.read()
        if not audio_bytes:
            return {"accepted": False, "error": "O audio gravado ficou vazio."}
        print(
            f"Transcricao iniciada: {len(audio_bytes)} bytes, tipo={content_type or 'desconhecido'}",
            flush=True,
        )
        if inference_active is not None:
            inference_active.set()
        try:
            text = await asyncio.to_thread(transcribe_audio_groq, audio_bytes, suffix)
        except Exception as error:
            print(f"Falha na transcricao: {error!r}", flush=True)
            return {"accepted": False, "error": str(error)}
        finally:
            if inference_active is not None:
                inference_active.clear()
        if not text:
            print("Transcricao concluida sem fala detectada.", flush=True)
            return {"accepted": False, "error": "Nenhuma fala detectada."}
        print(f"Transcricao concluida: {text}", flush=True)
        youtube_query = youtube_search_query(text)
        if youtube_query:
            command_queue.put_nowait(WebCommand("youtube_search", quote_plus(youtube_query)))
            state.add_chat_message(text, "outgoing")
            return {"accepted": True, "text": text, "youtube_search": youtube_query}
        try:
            chat_queue.put_nowait(("groq", text))
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


def run_web_server(
    command_queue,
    chat_queue,
    state,
    stop_event,
    virtual_input=None,
    inference_active=None,
):
    import uvicorn

    threading.Thread(
        target=capture_desktop_loop,
        args=(state, stop_event),
        daemon=True,
        name="desktop-capture",
    ).start()
    app = create_app(
        command_queue,
        chat_queue,
        state,
        stop_event,
        virtual_input,
        inference_active,
    )
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="info")