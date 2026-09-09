import os
import json
import queue
import shutil
import sys
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
import unicodedata
from datetime import datetime
from .web_server import RobotWebState, WebCommand, run_web_server
from .input_controller import create_virtual_input

os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import cv2
import serial
from picamera2 import Picamera2
try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

try:
    import mediapipe as mp
except ImportError:
    mp = None


WINDOW_NAME = "Visão do Zeta"
PROCESS_WIDTH = 640
PROCESS_HEIGHT = 480
DISPLAY_SCALE = 2
WINDOW_WIDTH = PROCESS_WIDTH * DISPLAY_SCALE
WINDOW_HEIGHT = PROCESS_HEIGHT * DISPLAY_SCALE
APP_DIR = os.path.dirname(__file__)
RASPBERRY_DIR = os.path.dirname(APP_DIR)
MODELS_DIR = os.path.join(RASPBERRY_DIR, "models")
HAND_MODEL_PATH = os.path.join(MODELS_DIR, "hand_landmarker.task")
HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
OBJECT_MODEL_PATH = os.path.join(MODELS_DIR, "yolo11n.pt")
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
)
# O Picamera2 com format="RGB888" entrega os bytes em ordem BGR (quirk conhecido);
# use --force-rgb-input se a sua fonte realmente entregar RGB puro.
FORCE_RGB_INPUT = "--force-rgb-input" in sys.argv
DETECTION_ENABLED = "--no-detect" not in sys.argv

# --- Configuracao dos servos via ESP32 (PWM em hardware, sem jitter) ---
# Ajuste a porta conforme aparece no seu Pi: rode "ls /dev/tty*" antes e
# depois de plugar o ESP32 por USB para descobrir. Costuma ser
# /dev/ttyUSB0 (chip CP2102/CH340) ou /dev/ttyACM0 (USB nativo).
SERIAL_PORT = "/dev/ttyUSB0"
SERIAL_BAUD = 115200
SERIAL_WRITE_TIMEOUT = 0.2
SERVO_SEND_INTERVAL = 0.05

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.environ.get(
    "OLLAMA_MODEL", # llama3.2:3b qwen2.5:3b - gemma3:4b - gemma3:1b
    os.environ.get("OLLAMA_MODAL", "qwen2.5:3b"),
)
OLLAMA_TIMEOUT_S = float(os.environ.get("OLLAMA_TIMEOUT_S", "180"))
OLLAMA_START_TIMEOUT_S = float(os.environ.get("OLLAMA_START_TIMEOUT_S", "15"))
OLLAMA_CONTEXT = (
    "Voce e o Zeta, um robo simpatico. Responda em portugues, de forma breve "
    "e natural, usando no maximo 240 caracteres."
)

SERVO_LIMIT = 0.8          # mesmo limite normalizado de -1.0 a 1.0
MANUAL_SERVO_STEP = 0.025
SERVO_HOLD_TIME = 0.4
FACE_TRACK_ENABLED = "--no-track" not in sys.argv
FACE_TRACK_SMOOTHING = 0.35    # suaviza (EMA) a posicao do rosto entre quadros
FACE_TRACK_DEADZONE = 0.06     # ignora desvios pequenos apos a suavizacao
FACE_TRACK_EASE = 0.15         # fracao do erro corrigida por quadro (movimento suave)
FACE_LOST_GRACE_FRAMES = 6     # mantem o ultimo alvo por alguns quadros ao perder o rosto
ROBOT_MODES = (
    "FOLLOW_HAND", "FOLLOW_FACE", "COUNT_FINGERS", "OBJECT_DETECTION", "DATE_TIME", "DRAWING",
)
DEFAULT_MODE = "FOLLOW_FACE"
GESTURE_REQUIRED_FRAMES = 6
GESTURE_COOLDOWN_S = 0.8
MENU_IDLE_TIMEOUT_S = 8.0
# TFLite/XNNPACK satura todos os nucleos durante a inferencia, o que pode
# impedir a thread de IPA da camera de rodar a tempo e travar capture_array().
# Rodar a deteccao pesada a cada N quadros da folga de CPU para a captura.
DETECTION_FRAME_INTERVAL = 1
OBJECT_DISPLAY_DELAY_S = 0.5  # atualiza as caixas de objetos no maximo duas vezes por segundo
SERVO_KEYS = {
    2424832: (-1.0, 0.0),
    2555904: (1.0, 0.0),
    2490368: (0.0, -1.0),
    2621440: (0.0, 1.0),
    ord("a"): (-1.0, 0.0),
    ord("d"): (1.0, 0.0),
    ord("w"): (0.0, -1.0),
    ord("s"): (0.0, 1.0),
    ord("A"): (-1.0, 0.0),
    ord("D"): (1.0, 0.0),
    ord("W"): (0.0, -1.0),
    ord("S"): (0.0, 1.0),
}


def value_to_angle(value):
    """Converte um valor normalizado (-1.0 a 1.0) para angulo (0 a 180)."""
    angle = 90 + (value * 90)
    return max(0, min(180, angle))


def create_servos():
    print(f"Conectando ao ESP32 em {SERIAL_PORT}...")
    ser = serial.Serial(
        SERIAL_PORT,
        SERIAL_BAUD,
        timeout=1,
        write_timeout=SERIAL_WRITE_TIMEOUT,
    )
    time.sleep(2)  # o ESP32 reseta ao abrir a porta serial; espera ele iniciar
    ser.reset_input_buffer()
    print("ESP32 conectado. Servos centralizados pelo firmware.")
    return ser


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


def send_servo_state(ser, state):
    now = time.monotonic()
    if now - state["last_servo_send"] < SERVO_SEND_INTERVAL:
        return
    x_angle = value_to_angle(state["x"])
    y_angle = value_to_angle(state["y"])
    try:
        ser.write(f"X:{x_angle},Y:{y_angle}\n".encode("ascii"))
        state["last_servo_send"] = now
    except serial.SerialTimeoutException:
        print("Aviso: escrita serial expirou; continuando o video.", file=sys.stderr)


def move_servos_manually(ser, state, direction_x, direction_y):
    state["x"] = clamp(state["x"] + direction_x * MANUAL_SERVO_STEP, -SERVO_LIMIT, SERVO_LIMIT)
    state["y"] = clamp(state["y"] + direction_y * MANUAL_SERVO_STEP, -SERVO_LIMIT, SERVO_LIMIT)
    send_servo_state(ser, state)


def face_bbox_to_target(bbox, frame_width, frame_height):
    x, y, width, height = bbox
    target_x = ((x + width / 2) / frame_width) * 2 - 1
    target_y = ((y + height / 2) / frame_height) * 2 - 1
    return target_x, target_y


def update_face_tracking(ser, state, target):
    """Suaviza o alvo (EMA) e move os servos gradualmente em direcao a ele."""
    target_x, target_y = target

    if state["smoothed_x"] is None:
        state["smoothed_x"], state["smoothed_y"] = target_x, target_y
    else:
        state["smoothed_x"] += (target_x - state["smoothed_x"]) * FACE_TRACK_SMOOTHING
        state["smoothed_y"] += (target_y - state["smoothed_y"]) * FACE_TRACK_SMOOTHING

    error_x = state["smoothed_x"] if abs(state["smoothed_x"]) >= FACE_TRACK_DEADZONE else 0.0
    error_y = state["smoothed_y"] if abs(state["smoothed_y"]) >= FACE_TRACK_DEADZONE else 0.0
    if error_x == 0.0 and error_y == 0.0:
        return

    state["x"] = clamp(state["x"] + error_x * FACE_TRACK_EASE, -SERVO_LIMIT, SERVO_LIMIT)
    state["y"] = clamp(state["y"] + error_y * FACE_TRACK_EASE, -SERVO_LIMIT, SERVO_LIMIT)
    send_servo_state(ser, state)


def stop_servos(ser):
    ser.write(b"OFF\n")


def send_robot_command(ser, command):
    """Envia uma mensagem de alto nivel para a interface do ESP32."""
    try:
        ser.write(f"{command}\n".encode("ascii"))
    except serial.SerialTimeoutException:
        print(f"Aviso: comando '{command}' expirou.", file=sys.stderr)


def send_status_text(ser, text):
    clean_text = " ".join(str(text).replace("\n", " ").replace("\r", " ").split())
    # O firmware usa a fonte ASCII do TFT; descarte apenas os diacriticos
    # combinados para transformar "nao" sem gerar "na?".
    clean_text = unicodedata.normalize("NFKD", clean_text).encode("ascii", "ignore").decode("ascii")
    send_robot_command(ser, "STATUS:TEXT:" + clean_text[:120])


def ensure_ollama_server():
    """Ensure the local Ollama API is ready without starting duplicate servers."""
    tags_url = OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=1.0):
            print(f"Ollama pronto. Modelo configurado: {OLLAMA_MODEL}")
            return
    except (OSError, urllib.error.URLError):
        pass

    ollama_host = urlparse(OLLAMA_URL).hostname
    if ollama_host not in ("127.0.0.1", "localhost", "::1"):
        print(f"Aviso: Ollama nao respondeu em {tags_url}.", file=sys.stderr)
        return

    ollama_executable = shutil.which("ollama")
    if ollama_executable is None:
        print(
            "Aviso: executavel 'ollama' nao encontrado; o chat ficara indisponivel.",
            file=sys.stderr,
        )
        return

    print("Ollama nao esta ativo. Iniciando ollama serve...")
    try:
        subprocess.Popen(
            [ollama_executable, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        print(f"Aviso: nao foi possivel iniciar o Ollama: {error}", file=sys.stderr)
        return

    deadline = time.monotonic() + OLLAMA_START_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(tags_url, timeout=1.0):
                print(f"Ollama pronto. Modelo configurado: {OLLAMA_MODEL}")
                return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    print("Aviso: Ollama nao iniciou dentro do tempo esperado.", file=sys.stderr)


def ask_ollama(prompt):
    ollama_executable = shutil.which("ollama")
    if ollama_executable is not None:
        cli_prompt = f"{OLLAMA_CONTEXT}\nUsuario: {prompt}\nZeta:"
        try:
            result = subprocess.run(
                [ollama_executable, "run", OLLAMA_MODEL, cli_prompt],
                capture_output=True,
                text=True,
                timeout=OLLAMA_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("ollama run excedeu o tempo limite") from error
        if result.returncode != 0:
            error_text = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(error_text or f"ollama run saiu com codigo {result.returncode}")
        response = result.stdout.strip()
        if response:
            return response

    chat_body = json.dumps(
        {
            "model": OLLAMA_MODEL,
            "stream": False,
            "messages": [
                {"role": "system", "content": OLLAMA_CONTEXT},
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    try:
        request = urllib.request.Request(
            OLLAMA_URL,
            data=chat_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload["message"]["content"].strip()
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace").strip()
        if error.code != 404:
            raise RuntimeError(f"HTTP {error.code}: {error_body or error.reason}") from error

        generate_url = OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/generate"
        generate_body = json.dumps(
            {
                "model": OLLAMA_MODEL,
                "prompt": f"{OLLAMA_CONTEXT}\nUsuario: {prompt}\nZeta:",
                "stream": False,
            }
        ).encode("utf-8")
        fallback_request = urllib.request.Request(
            generate_url,
            data=generate_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(fallback_request, timeout=OLLAMA_TIMEOUT_S) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as fallback_error:
            fallback_body = fallback_error.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"chat HTTP {error.code}: {error_body or error.reason}; "
                f"generate HTTP {fallback_error.code}: {fallback_body or fallback_error.reason}"
            ) from fallback_error
        return payload["response"].strip()


def ollama_worker(ser, prompt_queue, stop_event, inference_active, web_state=None):
    while not stop_event.is_set():
        try:
            prompt = prompt_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        try:
            print(f"Voce: {prompt}")
            send_status_text(ser, "Pensando...")
            if web_state is not None:
                web_state.update(chat_busy=True, last_message="Pensando...")
            inference_active.set()
            response = ask_ollama(prompt)
            if not response:
                response = "Nao consegui responder agora."
            print(f"Zeta: {response}")
            send_status_text(ser, response)
            if web_state is not None:
                web_state.update(chat_busy=False, last_message=response)
                web_state.add_chat_message(response, "incoming")
        except Exception as error:
            print(f"Aviso: Ollama indisponivel: {error}", file=sys.stderr)
            send_status_text(ser, "Nao consegui falar com o Ollama.")
            if web_state is not None:
                web_state.update(chat_busy=False, last_message="Nao consegui falar com o Ollama.")
                web_state.add_chat_message("Nao consegui falar com o Ollama.", "incoming")
        finally:
            inference_active.clear()
            prompt_queue.task_done()


def keyboard_chat_input(prompt_queue, stop_event):
    print("Chat ativo. Digite uma mensagem e pressione Enter.")
    while not stop_event.is_set():
        try:
            prompt = input("Voce> ").strip()
        except (EOFError, OSError):
            return
        if not prompt:
            continue
        try:
            prompt_queue.put_nowait(prompt)
            print("Zeta esta pensando...")
        except queue.Full:
            print("Zeta ainda esta respondendo; aguarde.")


def open_robot_menu(ser):
    send_robot_command(ser, "MODE:MENU")


MODE_INTRO_TEXT = {
    "FOLLOW_HAND": "Seguindo sua mao",
    "FOLLOW_FACE": "Seguindo seu rosto",
    "COUNT_FINGERS": "Contando dedos",
    "OBJECT_DETECTION": "Procurando objetos",
    "DATE_TIME": "Vendo as horas",
    "DRAWING": "Modo desenho",
}


def select_robot_mode(ser, mode):
    if mode not in ROBOT_MODES and mode != "FACE":
        return
    display_mode = "FACE" if mode in ("FOLLOW_HAND", "FOLLOW_FACE", "FACE") else mode
    send_robot_command(ser, f"MODE:{display_mode}")
    if mode in MODE_INTRO_TEXT:
        send_status_text(ser, MODE_INTRO_TEXT[mode])


# Watchdog de diagnostico: identifica em qual etapa do loop o programa
# ficou parado, sem depender de excecao ou de o usuario apertar Ctrl+C.
_STAGE_STALL_THRESHOLD_S = 4.0
_stage_lock = threading.Lock()
_current_stage = {"name": "iniciando", "since": time.monotonic()}


def _set_stage(name):
    with _stage_lock:
        _current_stage["name"] = name
        _current_stage["since"] = time.monotonic()


def _watchdog_loop(stop_event):
    warned_stage = None
    while not stop_event.is_set():
        stop_event.wait(1.0)
        with _stage_lock:
            name = _current_stage["name"]
            elapsed = time.monotonic() - _current_stage["since"]
        if elapsed >= _STAGE_STALL_THRESHOLD_S:
            if warned_stage != name:
                print(
                    f"Aviso: sem progresso ha {elapsed:.1f}s no estagio '{name}'; "
                    "possivel travamento nesta etapa.",
                    file=sys.stderr,
                )
                warned_stage = name
        else:
            warned_stage = None


def find_face_cascade():
    """Locate OpenCV's frontal-face Haar cascade."""
    cascade_name = "haarcascade_frontalface_default.xml"
    candidate_paths = []

    if hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
        candidate_paths.append(os.path.join(cv2.data.haarcascades, cascade_name))

    candidate_paths.extend(
        [
            os.path.join(
                os.path.dirname(cv2.__file__), "data", "haarcascades", cascade_name
            ),
            "/usr/share/opencv4/haarcascades/" + cascade_name,
            "/usr/share/opencv/haarcascades/" + cascade_name,
            os.path.join(MODELS_DIR, cascade_name),
        ]
    )

    cascade_path = next(
        (path for path in candidate_paths if os.path.exists(path)), None
    )

    if cascade_path is None:
        cascade_path = os.path.join(MODELS_DIR, cascade_name)
        url = (
            "https://raw.githubusercontent.com/opencv/opencv/"
            "4.x/data/haarcascades/haarcascade_frontalface_default.xml"
        )
        try:
            print("Cascade ausente. Baixando arquivo Haar...")
            urllib.request.urlretrieve(url, cascade_path)
        except Exception as error:
            raise FileNotFoundError(
                "Nao encontrei haarcascade_frontalface_default.xml e o download falhou. "
                "Instale o pacote opencv-data ou verifique a internet."
            ) from error

    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        raise RuntimeError("Nao foi possivel carregar o classificador Haar do rosto.")
    return cascade


def create_hand_detector():
    if mp is None:
        raise RuntimeError(
            "MediaPipe nao esta instalado. Execute: "
            "python3 -m pip install --break-system-packages mediapipe"
        )

    if not os.path.exists(HAND_MODEL_PATH):
        try:
            print("Modelo de mao ausente. Baixando hand_landmarker.task...")
            urllib.request.urlretrieve(HAND_MODEL_URL, HAND_MODEL_PATH)
        except Exception as error:
            raise RuntimeError(
                "Nao foi possivel baixar o modelo da mao. Verifique a internet."
            ) from error

    from mediapipe.tasks.python import vision

    options = vision.HandLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=HAND_MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
    )
    return vision.HandLandmarker.create_from_options(options)


def create_object_detector():
    if YOLO is None:
        raise RuntimeError("Ultralytics nao esta instalado para o modo de objetos.")
    if not os.path.exists(OBJECT_MODEL_PATH):
        raise FileNotFoundError(f"Modelo de objetos ausente: {OBJECT_MODEL_PATH}")
    return YOLO(OBJECT_MODEL_PATH)


def detect_objects(model, frame):
    results = model.predict(frame, imgsz=320, conf=0.35, verbose=False)
    detections = []
    for result in results:
        for box in result.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            class_id = int(box.cls[0])
            confidence = float(box.conf[0])
            label = result.names[class_id]
            detections.append((x1, y1, x2, y2, label, confidence))
    return detections


def draw_object_detections(frame, detections):
    for x1, y1, x2, y2, label, confidence in detections:
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 165, 255), 2)
        cv2.putText(
            frame,
            f"{label} {confidence:.2f}",
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 165, 255),
            2,
        )


def draw_hand(frame, hand_landmarks):
    height, width = frame.shape[:2]
    points = [
        (int(landmark.x * width), int(landmark.y * height))
        for landmark in hand_landmarks
    ]

    for start, end in HAND_CONNECTIONS:
        cv2.line(frame, points[start], points[end], (0, 255, 0), 2)
    for point in points:
        cv2.circle(frame, point, 5, (0, 0, 255), -1)

    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    x_min = max(0, min(x_values) - 12)
    y_min = max(0, min(y_values) - 12)
    x_max = min(width - 1, max(x_values) + 12)
    y_max = min(height - 1, max(y_values) + 12)
    cv2.rectangle(frame, (x_min, y_min), (x_max, y_max), (0, 255, 0), 2)
    cv2.putText(
        frame,
        "Mao",
        (x_min, max(22, y_min - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )


def draw_board(frame, drawing):
    """Desenha os tracos persistentes como linhas continuas."""
    previous_point = None
    for point in drawing:
        if point is None:
            previous_point = None
            continue
        if previous_point is not None:
            cv2.line(frame, previous_point, point, (0, 0, 255), 5, cv2.LINE_AA)
        previous_point = point


def count_extended_fingers(hand_landmarks, handedness=None):
    """Estima dedos estendidos, tratando o polegar conforme a mao detectada."""
    tips = (8, 12, 16, 20)
    pips = (6, 10, 14, 18)
    extended = sum(hand_landmarks[tip].y < hand_landmarks[pip].y for tip, pip in zip(tips, pips))
    thumb_tip = hand_landmarks[4]
    thumb_ip = hand_landmarks[3]
    thumb_mcp = hand_landmarks[2]
    palm_width = abs(hand_landmarks[5].x - hand_landmarks[17].x)
    thumb_reach = abs(thumb_tip.x - thumb_mcp.x)
    thumb_extended = thumb_reach > palm_width * 0.35
    return extended + int(thumb_extended)


def classify_hand_gesture(hand_landmarks):
    finger_extended = [
        hand_landmarks[tip].y < hand_landmarks[pip].y - 0.02
        for tip, pip in zip((8, 12, 16, 20), (6, 10, 14, 18))
    ]
    non_thumb_count = sum(finger_extended)
    if finger_extended[0] and finger_extended[1] and not any(finger_extended[2:]):
        return "peace"
    if non_thumb_count >= 3:
        return "open_palm"
    if non_thumb_count == 0:
        thumb_tip = hand_landmarks[4]
        thumb_ip = hand_landmarks[3]
        if thumb_tip.y < thumb_ip.y - 0.04:
            return "thumbs_up"
        if thumb_tip.y > hand_landmarks[0].y + 0.04:
            return "thumbs_down"
        return "closed_fist"
    index_tip = hand_landmarks[8]
    wrist = hand_landmarks[0]
    index_pip = hand_landmarks[6]
    index_is_extended = index_tip.y < index_pip.y - 0.03
    horizontal_reach = abs(index_tip.x - wrist.x)
    vertical_reach = wrist.y - index_tip.y
    if non_thumb_count == 1 and index_is_extended and vertical_reach > 0.12:
        return "point_up_right" if wrist.x >= 0.5 else "point_up_left"
    if non_thumb_count <= 2 and index_is_extended and horizontal_reach > 0.12:
        return "point_up_right" if wrist.x >= 0.5 else "point_up_left"
    return "none"


class GestureStabilizer:
    def __init__(self):
        self.last = None
        self.frames = 0

    def update(self, gesture):
        if gesture == self.last:
            self.frames += 1
        else:
            self.last = gesture
            self.frames = 1
        return gesture if self.frames == GESTURE_REQUIRED_FRAMES else None


def main():
    cv2.setNumThreads(1)  # evita que o OpenCV dispute nucleos com a thread de IPA da camera
    web_command_queue = queue.Queue()
    chat_queue = queue.Queue(maxsize=1)
    web_state = RobotWebState(DEFAULT_MODE)
    chat_stop = threading.Event()
    virtual_input = create_virtual_input()
    web_thread = threading.Thread(
        target=run_web_server,
        args=(web_command_queue, chat_queue, web_state, chat_stop, virtual_input),
        daemon=True,
    )
    web_thread.start()
    print("Controle web disponivel em http://IP_DO_RASPBERRY:8080")

    ensure_ollama_server()
    face_cascade = find_face_cascade() if DETECTION_ENABLED else None
    hand_detector = create_hand_detector() if DETECTION_ENABLED else None
    object_detector = None
    ser = create_servos()
    servo_state = {
        "x": 0.0,
        "y": 0.0,
        "smoothed_x": None,
        "smoothed_y": None,
        "lost_frames": 0,
        "last_servo_send": 0.0,
    }
    detach_at = None
    camera = None
    last_hand_timestamp_ms = 0
    frame_index = 0
    last_faces = ()
    last_hand_landmarks = None
    menu_index = 0
    menu_visible = False
    last_menu_interaction = 0.0
    robot_mode = DEFAULT_MODE
    gesture_stabilizer = GestureStabilizer()
    last_gesture_action = 0.0
    visible_gesture = "none"
    visible_finger_count = 0
    visible_object_detections = []
    drawing = []
    last_object_display = 0.0
    last_object_status_text = None
    last_status_send = 0.0
    web_state.update(mode=robot_mode)
    ollama_inference_active = threading.Event()
    chat_worker_thread = threading.Thread(
        target=ollama_worker,
        args=(ser, chat_queue, chat_stop, ollama_inference_active, web_state),
        daemon=True,
    )
    chat_input_thread = threading.Thread(
        target=keyboard_chat_input, args=(chat_queue, chat_stop), daemon=True
    )
    finger_candidate_count = None
    finger_candidate_since = 0.0
    last_finger_status_count = None
    watchdog_stop = threading.Event()
    watchdog_thread = threading.Thread(target=_watchdog_loop, args=(watchdog_stop,), daemon=True)
    watchdog_thread.start()
    chat_worker_thread.start()
    chat_input_thread.start()

    try:
        camera = Picamera2()
        camera.preview_configuration.main.size = (PROCESS_WIDTH, PROCESS_HEIGHT)
        camera.preview_configuration.main.format = "RGB888"
        camera.preview_configuration.align()
        camera.configure("preview")
        camera.start()
        camera.set_controls({"AeEnable": True, "AwbEnable": True})
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_NAME, WINDOW_WIDTH, WINDOW_HEIGHT)

        print("Reconhecimento ativo; controle manual por WASD ou setas. ESC para sair.")
        select_robot_mode(ser, robot_mode)
        print("Teste do menu: M abre; 1-6 selecionam uma funcao; F retorna ao rosto.")
        if not DETECTION_ENABLED:
            print("Deteccao desativada.")
        if not FACE_TRACK_ENABLED:
            print("Rastreamento automatico do rosto desativado.")
        if FORCE_RGB_INPUT:
            print("Entrada de camera tratada como RGB puro.")

        while True:
            while True:
                try:
                    web_command = web_command_queue.get_nowait()
                except queue.Empty:
                    break
                if web_command.kind == "mode":
                    robot_mode = web_command.value
                    menu_index = ROBOT_MODES.index(robot_mode)
                    if robot_mode == "DRAWING":
                        drawing.clear()
                    select_robot_mode(ser, robot_mode)
                    menu_visible = False
                elif web_command.kind == "stop":
                    stop_servos(ser)
                    detach_at = None
                elif web_command.kind == "servo":
                    try:
                        target_x, target_y = map(float, web_command.value.split(","))
                    except ValueError:
                        continue
                    servo_state["x"] = clamp(target_x, -SERVO_LIMIT, SERVO_LIMIT)
                    servo_state["y"] = clamp(target_y, -SERVO_LIMIT, SERVO_LIMIT)
                    servo_state["smoothed_x"] = None
                    servo_state["smoothed_y"] = None
                    servo_state["lost_frames"] = 0
                    detach_at = None
                    send_servo_state(ser, servo_state)

            _set_stage("captura_camera")
            captured_frame = camera.capture_array()
            if captured_frame is None:
                break

            _set_stage("conversao_cor")
            if FORCE_RGB_INPUT:
                frame_rgb = captured_frame
                frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
            else:
                frame_bgr = captured_frame
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

            frame_rgb = cv2.rotate(frame_rgb, cv2.ROTATE_180)
            frame_rgb = cv2.flip(frame_rgb, 1)
            frame_bgr = cv2.rotate(frame_bgr, cv2.ROTATE_180)
            frame_bgr = cv2.flip(frame_bgr, 1)
            detected = False
            frame_index += 1
            run_detection_this_frame = DETECTION_ENABLED and (
                frame_index % DETECTION_FRAME_INTERVAL == 0
            ) and not ollama_inference_active.is_set()

            if run_detection_this_frame:
                try:
                    _set_stage("deteccao_mao")
                    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                    # O MediaPipe exige timestamps estritamente crescentes; usar so
                    # time.monotonic() pode gerar o mesmo milissegundo em quadros
                    # seguidos e derrubar o processo com excecao nao tratada.
                    timestamp_ms = max(int(time.monotonic() * 1000), last_hand_timestamp_ms + 1)
                    last_hand_timestamp_ms = timestamp_ms
                    hand_results = hand_detector.detect_for_video(image, timestamp_ms)
                    if hand_results.hand_landmarks:
                        current_hand = hand_results.hand_landmarks[0]
                        handedness = None
                        if hand_results.handedness and hand_results.handedness[0]:
                            handedness = hand_results.handedness[0][0].category_name
                        draw_hand(frame_bgr, current_hand)
                        detected = True
                        last_hand_landmarks = current_hand
                        visible_finger_count = count_extended_fingers(current_hand, handedness)
                        visible_gesture = classify_hand_gesture(current_hand)
                        if robot_mode == "DRAWING":
                            if visible_finger_count == 1:
                                drawing.append(
                                    (int(current_hand[8].x * frame_bgr.shape[1]),
                                     int(current_hand[8].y * frame_bgr.shape[0]))
                                )
                            elif visible_finger_count == 3:
                                drawing.clear()
                            elif drawing and drawing[-1] is not None:
                                drawing.append(None)
                        stable_gesture = gesture_stabilizer.update(
                            visible_gesture
                        )
                        now = time.monotonic()
                        if stable_gesture and now - last_gesture_action >= GESTURE_COOLDOWN_S:
                            if not menu_visible and stable_gesture == "peace":
                                robot_mode = DEFAULT_MODE
                                menu_index = ROBOT_MODES.index(DEFAULT_MODE)
                                open_robot_menu(ser)
                                menu_visible = True
                                last_menu_interaction = now
                                last_gesture_action = now
                            elif menu_visible and stable_gesture == "point_up_left":
                                menu_index = (menu_index + 1) % len(ROBOT_MODES)
                                send_robot_command(ser, f"MENU:INDEX:{menu_index}")
                                last_menu_interaction = now
                                last_gesture_action = now
                            elif menu_visible and stable_gesture == "point_up_right":
                                menu_index = (menu_index - 1) % len(ROBOT_MODES)
                                send_robot_command(ser, f"MENU:INDEX:{menu_index}")
                                last_menu_interaction = now
                                last_gesture_action = now
                            elif menu_visible and stable_gesture in ("thumbs_up", "closed_fist"):
                                send_robot_command(ser, "MENU:CONFIRM")
                                robot_mode = ROBOT_MODES[menu_index]
                                if robot_mode == "DRAWING":
                                    drawing.clear()
                                servo_state["lost_frames"] = 0
                                detach_at = None
                                select_robot_mode(ser, robot_mode)
                                menu_visible = False
                                last_gesture_action = now
                            elif menu_visible and stable_gesture == "thumbs_down":
                                send_robot_command(ser, "MENU:CANCEL")
                                menu_visible = False
                                last_gesture_action = now
                    else:
                        last_hand_landmarks = None
                        visible_gesture = "none"
                        visible_finger_count = 0
                        if robot_mode == "DRAWING" and drawing and drawing[-1] is not None:
                            drawing.append(None)
                        gesture_stabilizer.update("none")

                    if menu_visible and time.monotonic() - last_menu_interaction >= MENU_IDLE_TIMEOUT_S:
                        send_robot_command(ser, "MODE:FACE")
                        menu_visible = False

                    _set_stage("deteccao_rosto")
                    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                    faces = () if robot_mode == "OBJECT_DETECTION" else face_cascade.detectMultiScale(
                        gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40)
                    )
                    for x, y, width, height in faces:
                        cv2.rectangle(
                            frame_bgr,
                            (x, y),
                            (x + width, y + height),
                            (255, 0, 0),
                            2,
                        )
                        cv2.putText(
                            frame_bgr,
                            "Humano",
                            (x, max(22, y - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            (255, 0, 0),
                            2,
                            cv2.LINE_AA,
                        )
                    detected = detected or len(faces) > 0
                    last_faces = faces

                    if robot_mode == "OBJECT_DETECTION":
                        _set_stage("deteccao_objetos")
                        if object_detector is None:
                            object_detector = create_object_detector()
                        object_detections = detect_objects(object_detector, frame_bgr)
                        now = time.monotonic()
                        if now - last_object_display >= OBJECT_DISPLAY_DELAY_S:
                            visible_object_detections = object_detections
                            last_object_display = now
                        labels = [detection[4] for detection in object_detections]
                        object_status_text = "Objetos: " + ", ".join(sorted(set(labels))) if labels else None
                        if (
                            object_status_text
                            and object_status_text != last_object_status_text
                            and now - last_status_send >= 1.0
                        ):
                            send_status_text(ser, object_status_text)
                            last_object_status_text = object_status_text
                            last_status_send = now

                    _set_stage("rastreamento_servos")
                    if robot_mode == "FOLLOW_FACE" and FACE_TRACK_ENABLED and detach_at is None:
                        if len(faces) > 0:
                            largest_face = max(faces, key=lambda face: face[2] * face[3])
                            servo_state["lost_frames"] = 0
                            update_face_tracking(
                                ser, servo_state, face_bbox_to_target(
                                    largest_face, frame_bgr.shape[1], frame_bgr.shape[0]
                                )
                            )
                        elif (
                            servo_state["smoothed_x"] is not None
                            and servo_state["lost_frames"] < FACE_LOST_GRACE_FRAMES
                        ):
                            servo_state["lost_frames"] += 1
                            update_face_tracking(
                                ser, servo_state, (servo_state["smoothed_x"], servo_state["smoothed_y"])
                            )
                    if robot_mode == "FOLLOW_HAND" and last_hand_landmarks is not None:
                        hand_tip = last_hand_landmarks[8]
                        update_face_tracking(
                            ser, servo_state,
                            (hand_tip.x * 2 - 1, hand_tip.y * 2 - 1),
                        )
                    now = time.monotonic()
                    if robot_mode == "COUNT_FINGERS" and last_hand_landmarks is not None:
                        if visible_finger_count != finger_candidate_count:
                            finger_candidate_count = visible_finger_count
                            finger_candidate_since = now
                        elif (
                            now - finger_candidate_since >= 0.4
                            and visible_finger_count != last_finger_status_count
                        ):
                            send_robot_command(ser, f"STATUS:FINGERS:{visible_finger_count}")
                            last_finger_status_count = visible_finger_count
                    elif robot_mode == "COUNT_FINGERS":
                        if finger_candidate_count != 0:
                            finger_candidate_count = 0
                            finger_candidate_since = now
                        elif now - finger_candidate_since >= 0.4 and last_finger_status_count != 0:
                            send_robot_command(ser, "STATUS:FINGERS:0")
                            last_finger_status_count = 0
                    elif robot_mode == "DATE_TIME" and now - last_status_send >= 1.0:
                        send_status_text(ser, datetime.now().strftime("%d/%m/%Y  %H:%M"))
                        last_status_send = now

                    # MediaPipe 1.0.1 libera esses buffers via GC; remova as
                    # referencias imediatamente para evitar acumulo durante a captura.
                    del hand_results, image
                except Exception as detection_error:
                    # Uma falha pontual de deteccao nao pode derrubar o loop
                    # principal, senao a janela congela e os servos param.
                    print(f"Aviso: deteccao falhou neste quadro: {detection_error}", file=sys.stderr)
            elif DETECTION_ENABLED:
                # Reaproveita o ultimo resultado nos quadros sem deteccao pesada,
                # so para manter a sobreposicao visual sem piscar.
                for x, y, width, height in last_faces:
                    cv2.rectangle(frame_bgr, (x, y), (x + width, y + height), (255, 0, 0), 2)
                    cv2.putText(
                        frame_bgr,
                        "Humano",
                        (x, max(22, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (255, 0, 0),
                        2,
                        cv2.LINE_AA,
                    )
                if last_hand_landmarks is not None:
                    draw_hand(frame_bgr, last_hand_landmarks)

            if robot_mode == "OBJECT_DETECTION":
                draw_object_detections(frame_bgr, visible_object_detections)
            elif robot_mode == "DRAWING":
                draw_board(frame_bgr, drawing)

            web_state.update(
                mode=robot_mode,
                gesture=visible_gesture,
                finger_count=visible_finger_count,
                objects=sorted({detection[4] for detection in visible_object_detections}),
            )
            encoded_frame = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if encoded_frame[0]:
                web_state.update(frame=encoded_frame[1].tobytes())

            display_frame = cv2.resize(
                frame_bgr, (WINDOW_WIDTH, WINDOW_HEIGHT), interpolation=cv2.INTER_NEAREST
            )
            cv2.putText(
                display_frame,
                f"gesto: {visible_gesture}  dedos: {visible_finger_count}",
                (16, 32),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
            _set_stage("exibicao_janela")
            cv2.imshow(WINDOW_NAME, display_frame)
            key = cv2.waitKeyEx(20)
            if key == 27:
                break
            normalized_key = key & 0xFF
            if normalized_key in (ord("m"), ord("M")):
                robot_mode = DEFAULT_MODE
                menu_index = ROBOT_MODES.index(DEFAULT_MODE)
                open_robot_menu(ser)
                menu_visible = True
                last_menu_interaction = time.monotonic()
                continue
            if normalized_key in (ord("f"), ord("F")):
                robot_mode = DEFAULT_MODE
                select_robot_mode(ser, "FACE")
                continue
            if normalized_key in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5"), ord("6")):
                menu_index = int(chr(normalized_key)) - 1
                send_robot_command(ser, f"MENU:INDEX:{menu_index}")
                robot_mode = ROBOT_MODES[menu_index]
                if robot_mode == "DRAWING":
                    drawing.clear()
                select_robot_mode(ser, robot_mode)
                menu_visible = False
                continue
            if key & 0xFF == ord(" "):
                stop_servos(ser)
                detach_at = None
                print("Servos parados.")
                continue
            # Alguns backends retornam teclas com bits extras; o byte baixo
            # preserva o codigo ASCII de W, A, S e D.
            movement = SERVO_KEYS.get(key, SERVO_KEYS.get(normalized_key))
            if movement is not None:
                move_servos_manually(ser, servo_state, *movement)
                detach_at = time.monotonic() + SERVO_HOLD_TIME

            # Libera o sinal apos ficar parado (o ESP32 nao gera jitter,
            # mas isso evita aquecimento desnecessario do servo em repouso).
            if detach_at is not None and time.monotonic() >= detach_at:
                stop_servos(ser)
                detach_at = None
    finally:
        chat_stop.set()
        watchdog_stop.set()
        if hand_detector is not None:
            hand_detector.close()
        if camera is not None:
            camera.stop()
        if virtual_input is not None:
            virtual_input.close()
        cv2.destroyAllWindows()
        try:
            stop_servos(ser)
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nEncerrado pelo usuario.")
    except Exception as error:
        print(f"Erro: {error}", file=sys.stderr)
        sys.exit(1)