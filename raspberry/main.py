import os
import sys
import time
import urllib.request

import cv2
import serial
from picamera2 import Picamera2

try:
    import mediapipe as mp
except ImportError:
    mp = None


WINDOW_NAME = "Detector de rosto e mao"
PROCESS_WIDTH = 640
PROCESS_HEIGHT = 480
WINDOW_HEIGHT = 720
WINDOW_WIDTH = int(WINDOW_HEIGHT * PROCESS_WIDTH / PROCESS_HEIGHT)
HAND_MODEL_PATH = os.path.join(os.getcwd(), "hand_landmarker.task")
HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
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

SERVO_LIMIT = 0.8          # mesmo limite normalizado de -1.0 a 1.0
MANUAL_SERVO_STEP = 0.025
SERVO_HOLD_TIME = 0.4
FACE_TRACK_ENABLED = "--no-track" not in sys.argv
FACE_TRACK_SMOOTHING = 0.35    # suaviza (EMA) a posicao do rosto entre quadros
FACE_TRACK_DEADZONE = 0.06     # ignora desvios pequenos apos a suavizacao
FACE_TRACK_EASE = 0.15         # fracao do erro corrigida por quadro (movimento suave)
FACE_LOST_GRACE_FRAMES = 6     # mantem o ultimo alvo por alguns quadros ao perder o rosto
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
            os.path.join(os.getcwd(), cascade_name),
        ]
    )

    cascade_path = next(
        (path for path in candidate_paths if os.path.exists(path)), None
    )

    if cascade_path is None:
        cascade_path = os.path.join(os.getcwd(), cascade_name)
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
        running_mode=vision.RunningMode.IMAGE,
        num_hands=1,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
    )
    return vision.HandLandmarker.create_from_options(options)


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


def main():
    face_cascade = find_face_cascade() if DETECTION_ENABLED else None
    hand_detector = create_hand_detector() if DETECTION_ENABLED else None
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
        if not DETECTION_ENABLED:
            print("Deteccao desativada.")
        if not FACE_TRACK_ENABLED:
            print("Rastreamento automatico do rosto desativado.")
        if FORCE_RGB_INPUT:
            print("Entrada de camera tratada como RGB puro.")

        while True:
            captured_frame = camera.capture_array()
            if captured_frame is None:
                break

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

            if DETECTION_ENABLED:
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                hand_results = hand_detector.detect(image)
                if hand_results.hand_landmarks:
                    draw_hand(frame_bgr, hand_results.hand_landmarks[0])
                    detected = True

                gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=5,
                    minSize=(40, 40),
                )
                for x, y, width, height in faces:
                    cv2.rectangle(
                        frame_bgr,
                        (x, y),
                        (x + width, y + height),
                        (255, 0, 0),
                        2,
                    )
                detected = detected or len(faces) > 0

                if FACE_TRACK_ENABLED and detach_at is None:
                    if len(faces) > 0:
                        largest_face = max(faces, key=lambda face: face[2] * face[3])
                        servo_state["lost_frames"] = 0
                        update_face_tracking(
                            ser, servo_state, face_bbox_to_target(largest_face, PROCESS_WIDTH, PROCESS_HEIGHT)
                        )
                    elif (
                        servo_state["smoothed_x"] is not None
                        and servo_state["lost_frames"] < FACE_LOST_GRACE_FRAMES
                    ):
                        servo_state["lost_frames"] += 1
                        update_face_tracking(
                            ser, servo_state, (servo_state["smoothed_x"], servo_state["smoothed_y"])
                        )

                # MediaPipe 1.0.1 libera esses buffers via GC; remova as
                # referencias imediatamente para evitar acumulo durante a captura.
                del hand_results, image

            display_frame = cv2.resize(
                frame_bgr, (WINDOW_WIDTH, WINDOW_HEIGHT), interpolation=cv2.INTER_NEAREST
            )
            cv2.imshow(WINDOW_NAME, display_frame)
            key = cv2.waitKeyEx(20)
            if key == 27:
                break
            if key & 0xFF == ord(" "):
                stop_servos(ser)
                detach_at = None
                print("Servos parados.")
                continue
            # Alguns backends retornam teclas com bits extras; o byte baixo
            # preserva o codigo ASCII de W, A, S e D.
            normalized_key = key & 0xFF
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
        if hand_detector is not None:
            hand_detector.close()
        if camera is not None:
            camera.stop()
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