import os
import cv2
import numpy as np

from modlib.devices import AiCamera
from modlib.models import COLOR_FORMAT, MODEL_TYPE, Model


# ============================================================
# CONFIGURAÇÕES
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_DIR = os.path.join(
    BASE_DIR,
    "yolo11n-pose_imx_model"
)

MODEL_FILE = os.path.join(
    MODEL_DIR,
    "packerOut.zip"
)

FRAME_RATE = 12

# O artefato IMX500 declara entrada e saída espacial em 640x640.
MODEL_WIDTH = 640.0
MODEL_HEIGHT = 640.0

PERSON_CONFIDENCE = 0.35
KEYPOINT_CONFIDENCE = 0.20

WINDOW_NAME = "YOLO11n Pose - IMX500"


# ============================================================
# YOLO11 POSE
# ============================================================

class PoseModel(Model):

    def __init__(self):

        super().__init__(
            model_file=MODEL_FILE,
            model_type=MODEL_TYPE.CONVERTED,
            color_format=COLOR_FORMAT.RGB,
            preserve_aspect_ratio=False,
        )

        self.latest_result = None

    def post_process(self, output_tensors):

        boxes = np.asarray(output_tensors[0])
        scores = np.asarray(output_tensors[1])
        classes = np.asarray(output_tensors[2])
        keypoints = np.asarray(output_tensors[3])

        self.latest_result = {
            "boxes": boxes,
            "scores": scores,
            "classes": classes,
            "keypoints": keypoints,
        }

        return self.latest_result


# ============================================================
# DESENHA POSE
# ============================================================

def draw_pose(frame, result):

    if result is None:
        return 0

    boxes = result["boxes"]
    scores = result["scores"]
    classes = result["classes"]
    keypoints = result["keypoints"]

    height, width = frame.shape[:2]

    person_count = 0

    def project_coordinate(value, model_size, frame_size):
        value = float(value)
        if 0.0 <= value <= 1.0:
            value *= model_size
        return max(0, min(frame_size - 1, int(value / model_size * frame_size)))

    # ========================================================
    # SKELETON COCO - 17 KEYPOINTS
    # ========================================================

    skeleton = [
        # Cabeça
        (0, 1),
        (0, 2),
        (1, 3),
        (2, 4),

        # Ombros
        (5, 6),

        # Braço esquerdo
        (5, 7),
        (7, 9),

        # Braço direito
        (6, 8),
        (8, 10),

        # Tronco
        (5, 11),
        (6, 12),
        (11, 12),

        # Perna esquerda
        (11, 13),
        (13, 15),

        # Perna direita
        (12, 14),
        (14, 16),
    ]

    # ========================================================
    # PROCESSA CADA DETECÇÃO
    # ========================================================

    valid_indices = [
        index
        for index, score in enumerate(scores)
        if float(score) >= PERSON_CONFIDENCE
        and int(classes[index]) == 0
    ]

    if not valid_indices:
        return 0

    # O output convertido pode conter várias caixas sobrepostas para a
    # mesma pessoa. Usa a melhor detecção para manter pose e caixa juntas.
    selected_indices = [
        max(valid_indices, key=lambda index: float(scores[index]))
    ]

    for i in selected_indices:

        score = float(scores[i])

        person_count += 1

        # ====================================================
        # BOUNDING BOX
        # ====================================================

        x1, y1, x2, y2 = boxes[i]

        x1 = project_coordinate(x1, MODEL_WIDTH, width)
        x2 = project_coordinate(x2, MODEL_WIDTH, width)
        y1 = project_coordinate(y1, MODEL_HEIGHT, height)
        y2 = project_coordinate(y2, MODEL_HEIGHT, height)

        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            (0, 255, 0),
            2,
        )

        cv2.putText(
            frame,
            f"Person {score:.2f}",
            (x1, max(25, y1 - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
        )

        # ====================================================
        # KEYPOINTS
        # ====================================================

        kp = keypoints[i].reshape(17, 3)

        points = [None] * 17

        for index, (x, y, confidence) in enumerate(kp):

            confidence = float(confidence)

            if confidence < KEYPOINT_CONFIDENCE:
                continue

            # ------------------------------------------------
            # IMPORTANTE
            #
            # O modelo fornece:
            #
            # X = 0..640
            # Y = 0..640
            #
            # Convertemos para:
            #
            # X = 0..640
            # Y = 0..480
            # ------------------------------------------------

            px = project_coordinate(x, MODEL_WIDTH, width)
            py = project_coordinate(y, MODEL_HEIGHT, height)

            points[index] = (px, py)

            # ------------------------------------------------
            # PONTO
            # ------------------------------------------------

            cv2.circle(
                frame,
                (px, py),
                5,
                (0, 0, 255),
                -1,
            )

            # Número do ponto
            cv2.putText(
                frame,
                str(index),
                (px + 7, py - 7),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (255, 255, 255),
                1,
            )

        # ====================================================
        # DESENHA ESQUELETO
        # ====================================================

        for a, b in skeleton:

            if points[a] is None:
                continue

            if points[b] is None:
                continue

            cv2.line(
                frame,
                points[a],
                points[b],
                (255, 0, 0),
                2,
            )

    return person_count


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("==========================================")
    print("       YOLO11n-Pose - IMX500")
    print("==========================================")
    print()

    print("Modelo:")
    print(MODEL_FILE)
    print()

    # ========================================================
    # VERIFICA MODELO
    # ========================================================

    if not os.path.exists(MODEL_FILE):

        raise FileNotFoundError(
            f"Modelo não encontrado:\n{MODEL_FILE}"
        )

    print("Modelo encontrado.")
    print()

    # ========================================================
    # CRIA MODELO
    # ========================================================

    print("Carregando modelo...")

    model = PoseModel()

    print("Modelo carregado.")
    print()

    # ========================================================
    # CAMERA
    # ========================================================

    print("Iniciando câmera...")

    device = AiCamera(
        frame_rate=FRAME_RATE
    )

    print("Deploy do YOLO11n-Pose...")

    device.deploy(model)

    print()
    print("Câmera configurada.")
    print()
    print("ESC = sair")
    print()

    # ========================================================
    # JANELA
    # ========================================================

    cv2.namedWindow(
        WINDOW_NAME,
        cv2.WINDOW_NORMAL
    )

    cv2.resizeWindow(
        WINDOW_NAME,
        960,
        720
    )

    # ========================================================
    # STREAM
    #
    # Usamos exatamente o fluxo que já funcionou
    # no seu ambiente.
    # ========================================================

    try:

        with device as stream:

            print("Stream iniciado.")
            print()

            for frame in stream:

                # =================================================
                # FRAME DA CAMERA
                #
                # NÃO fazemos RGB2BGR.
                #
                # Você confirmou que esta configuração produz
                # as cores corretas.
                # =================================================

                display = frame.image.copy()

                # =================================================
                # DETECÇÃO + POSE
                #
                # Primeiro desenhamos no frame original.
                # Depois fazemos a rotação.
                # =================================================

                person_count = draw_pose(
                    display,
                    model.latest_result
                )

                # =================================================
                # CORREÇÃO DA ORIENTAÇÃO DA CAMERA
                #
                # Sua câmera está de ponta-cabeça.
                # =================================================

                display = cv2.rotate(
                    display,
                    cv2.ROTATE_180
                )

                # =================================================
                # INFORMAÇÕES
                # =================================================

                cv2.putText(
                    display,
                    f"Pessoas: {person_count}",
                    (15, 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (0, 255, 255),
                    2,
                )

                cv2.putText(
                    display,
                    "YOLO11n-Pose | IMX500",
                    (15, 65),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    2,
                )

                # =================================================
                # EXIBIÇÃO
                # =================================================

                cv2.imshow(
                    WINDOW_NAME,
                    display
                )

                # =================================================
                # TECLADO
                # =================================================

                key = cv2.waitKey(1) & 0xFF

                if key == 27:
                    break

                if key == ord("q"):
                    break

    except KeyboardInterrupt:

        print()
        print("Interrompido pelo usuário.")

    finally:

        print()
        print("Encerrando câmera...")

        try:
            device.close()
        except Exception:
            pass

        cv2.destroyAllWindows()

        print("Programa encerrado.")


# ============================================================
# EXECUTA
# ============================================================

if __name__ == "__main__":
    main()