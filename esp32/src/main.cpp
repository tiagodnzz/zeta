// Controlador de servos + olho animado (TFT) para ESP32 (PlatformIO)
// Recebe comandos via Serial (USB) do Raspberry Pi e move os servos
// usando PWM em hardware (LEDC) - sem jitter, independente de carga de CPU.
// O mesmo angulo recebido tambem move a iris/pupila desenhada no TFT.
//
// Protocolo (uma linha por comando, terminada em '\n'):
//   X:<0-180>,Y:<0-180>   -> move os dois servos para os angulos dados
//   OFF                    -> solta o sinal dos dois servos (para de segurar)

#include <Arduino.h>
#include <ESP32Servo.h>
#include <TFT_eSPI.h>

namespace {

constexpr int kPinServoX = 25;
constexpr int kPinServoY = 26;

constexpr int kPulseMinUs = 500;
constexpr int kPulseMaxUs = 2500;

// Tela 1.69" montada deitada; esta orientacao corrige a imagem invertida.
constexpr uint8_t kScreenRotation = 3;
constexpr uint16_t kEyeColor = TFT_CYAN;
constexpr uint16_t kBackgroundColor = TFT_BLACK;
constexpr uint16_t kMouthColor = TFT_CYAN;
constexpr unsigned long kIdleDelayMs = 1800;
constexpr unsigned long kIdleCycleMs = 20500;

Servo g_servoX;
Servo g_servoY;
TFT_eSPI g_tft;

String g_inputLine;

int16_t g_leftEyeCenterX = 0;
int16_t g_rightEyeCenterX = 0;
int16_t g_eyeCenterY = 0;
int16_t g_eyeWidth = 0;
int16_t g_eyeHeight = 0;
int16_t g_eyeCornerRadius = 0;
int16_t g_maxOffset = 0;
int16_t g_currentOffsetX = 0;
int16_t g_currentOffsetY = 0;
unsigned long g_lastFaceFrame = 0;
unsigned long g_lastTrackingCommand = 0;
unsigned long g_lastZFrame = 0;
bool g_lastBlinking = false;
int8_t g_idlePhase = -1;

void attachIfNeeded() {
  if (!g_servoX.attached()) {
    g_servoX.attach(kPinServoX, kPulseMinUs, kPulseMaxUs);
  }
  if (!g_servoY.attached()) {
    g_servoY.attach(kPinServoY, kPulseMinUs, kPulseMaxUs);
  }
}

void drawEyeShape(int16_t centerX, int16_t centerY, uint16_t color, bool blinking) {
  const int16_t height = blinking ? 5 : g_eyeHeight;
  const int16_t cornerRadius = height / 2 < g_eyeCornerRadius ? height / 2 : g_eyeCornerRadius;
  g_tft.fillRoundRect(centerX - g_eyeWidth / 2, centerY - height / 2,
                       g_eyeWidth, height, cornerRadius, color);
}

void drawMouth() {
  const int16_t mouthCenterX = g_tft.width() / 2;
  const int16_t mouthY = g_eyeCenterY + g_eyeHeight / 2 + 28;
  const int16_t mouthWidth = 16;
  g_tft.fillRoundRect(mouthCenterX - mouthWidth, mouthY, mouthWidth * 2, 5, 2, kMouthColor);
}

void drawFace(bool blinking) {
  g_tft.fillScreen(kBackgroundColor);
  drawEyeShape(g_leftEyeCenterX + g_currentOffsetX, g_eyeCenterY + g_currentOffsetY,
               kEyeColor, blinking);
  drawEyeShape(g_rightEyeCenterX + g_currentOffsetX, g_eyeCenterY + g_currentOffsetY,
               kEyeColor, blinking);
  drawMouth();
}

void drawIdleFace(int16_t eyeOffsetX, bool blinking, bool yawning, bool smiling = false) {
  g_tft.fillScreen(kBackgroundColor);
  drawEyeShape(g_leftEyeCenterX + eyeOffsetX, g_eyeCenterY, kEyeColor, blinking);
  drawEyeShape(g_rightEyeCenterX + eyeOffsetX, g_eyeCenterY, kEyeColor, blinking);

  const int16_t mouthCenterX = g_tft.width() / 2;
  const int16_t mouthY = g_eyeCenterY + g_eyeHeight / 2 + 28;
  if (yawning) {
    g_tft.fillEllipse(mouthCenterX, mouthY + 3, 10, 13, kMouthColor);
  } else if (smiling) {
    for (int16_t x = -16; x < 17; x += 3) {
      const int16_t y = mouthY - (x * x) / 40;
      g_tft.fillCircle(mouthCenterX + x, y, 2, kMouthColor);
    }
  } else {
    g_tft.fillRoundRect(mouthCenterX - 16, mouthY, 32, 5, 2, kMouthColor);
  }
}

void drawSleepZ(unsigned long now);

void drawSleepingFace(unsigned long now) {
  g_tft.fillScreen(kBackgroundColor);
  drawEyeShape(g_leftEyeCenterX, g_eyeCenterY, kEyeColor, true);
  drawEyeShape(g_rightEyeCenterX, g_eyeCenterY, kEyeColor, true);

  drawSleepZ(now);
}

void drawSleepZ(unsigned long now) {
  const int16_t zAreaLeft = g_leftEyeCenterX - 18;
  const int16_t zAreaTop = g_eyeCenterY - g_eyeHeight / 2 - 48;
  g_tft.fillRect(zAreaLeft - 6, zAreaTop - 4, 72, 56, kBackgroundColor);
  g_tft.setTextColor(kEyeColor, kBackgroundColor);
  g_tft.setTextSize(2);
  const int8_t zStep = (now / 180) % 18;
  const int8_t visibleZ = (now / 600) % 4;
  for (int8_t index = 0; index < 3; index++) {
    if (visibleZ == index) {
      const int16_t x = zAreaLeft + index * 20;
      const int16_t y = zAreaTop + 34 - zStep - index * 8;
      g_tft.setCursor(x, y < 2 ? 2 : y);
      g_tft.print("Z");
    }
  }
}

void redrawEyes(bool blinking) {
  const int16_t padding = 3;
  const int16_t eyeRegionTop = g_eyeCenterY - g_maxOffset - g_eyeHeight / 2 - padding;
  const int16_t eyeRegionBottom = g_eyeCenterY + g_maxOffset + g_eyeHeight / 2 + padding;
  const int16_t eyeRegionHeight = eyeRegionBottom - eyeRegionTop;
  const int16_t eyeRegionWidth = g_tft.width() / 2 - 2 * padding;

  g_tft.fillRect(padding, eyeRegionTop, eyeRegionWidth, eyeRegionHeight, kBackgroundColor);
  g_tft.fillRect(g_tft.width() / 2 + padding, eyeRegionTop, eyeRegionWidth,
                 eyeRegionHeight, kBackgroundColor);
  drawEyeShape(g_leftEyeCenterX + g_currentOffsetX, g_eyeCenterY + g_currentOffsetY,
               kEyeColor, blinking);
  drawEyeShape(g_rightEyeCenterX + g_currentOffsetX, g_eyeCenterY + g_currentOffsetY,
               kEyeColor, blinking);
}

void drawEyeBase() {
  const int16_t screenWidth = g_tft.width();
  const int16_t screenHeight = g_tft.height();
  const int16_t halfWidth = screenWidth / 2;

  g_eyeCenterY = screenHeight / 2 - 15;
  g_leftEyeCenterX = halfWidth / 2;
  g_rightEyeCenterX = halfWidth + halfWidth / 2;
  g_eyeWidth = halfWidth * 0.45f;
  g_eyeHeight = g_eyeWidth * 1.6f;
  g_eyeCornerRadius = g_eyeWidth / 2;
  g_maxOffset = min(halfWidth - g_eyeWidth, screenHeight - g_eyeHeight) / 2 - 6;

  drawFace(false);
}

// Desloca os dois olhos juntos proporcionalmente ao angulo (0-180, 90 = centro).
void updateEyeTarget(int xAngle, int yAngle) {
  const float normX = constrain((xAngle - 90) / 90.0f, -1.0f, 1.0f);
  const float normY = constrain((yAngle - 90) / 90.0f, -1.0f, 1.0f);
  const int16_t targetOffsetX = static_cast<int16_t>(normX * g_maxOffset);
  const int16_t targetOffsetY = static_cast<int16_t>(normY * g_maxOffset);

  g_currentOffsetX = targetOffsetX;
  g_currentOffsetY = targetOffsetY;
  redrawEyes(g_lastBlinking);
}

void animateFace() {
  const unsigned long now = millis();
  if (now - g_lastFaceFrame < 40) {
    return;
  }
  g_lastFaceFrame = now;

  if (now - g_lastTrackingCommand < kIdleDelayMs) {
    const unsigned long blinkPosition = now % 5000;
    const bool blinking = blinkPosition >= 4200 && blinkPosition < 4350;
    if (blinking != g_lastBlinking) {
      g_lastBlinking = blinking;
      drawFace(blinking);
    }
    return;
  }

  const unsigned long idlePosition = (now - g_lastTrackingCommand) % kIdleCycleMs;
  int8_t phase = 0;
  if (idlePosition >= 2500 && idlePosition < 5500) {
    phase = 1;
  } else if (idlePosition >= 5500 && idlePosition < 5900) {
    phase = 2;
  } else if (idlePosition >= 5900 && idlePosition < 7500) {
    phase = 3;
  } else if (idlePosition >= 7500 && idlePosition < 10000) {
    phase = 4;
  } else if (idlePosition >= 10000 && idlePosition < 11000) {
    phase = 5;
  } else if (idlePosition >= 11000 && idlePosition < 19500) {
    phase = 6;
  } else if (idlePosition >= 19500) {
    phase = 7;
  }

  if (phase == g_idlePhase) {
    if (phase == 6 && now - g_lastZFrame >= 180) {
      g_lastZFrame = now;
      drawSleepZ(now);
    }
    return;
  }
  g_idlePhase = phase;

  if (phase == 1) {
    g_currentOffsetX = g_maxOffset * 0.7f;
    drawIdleFace(g_currentOffsetX, false, false);
  } else if (phase == 2) {
    drawIdleFace(g_currentOffsetX, true, false);
  } else if (phase == 3) {
    g_currentOffsetX = g_maxOffset * 0.35f;
    drawIdleFace(g_currentOffsetX, false, false);
  } else if (phase == 4) {
    drawIdleFace(g_currentOffsetX, false, true);
  } else if (phase == 5) {
    drawSleepingFace(now);
  } else if (phase == 6) {
    drawSleepingFace(now);
  } else if (phase == 7) {
    g_currentOffsetX = 0;
    drawIdleFace(0, false, false, true);
  } else {
    g_currentOffsetX = 0;
    drawIdleFace(0, false, false);
  }
}

void processCommand(String line) {
  line.trim();
  g_lastTrackingCommand = millis();
  g_idlePhase = -1;

  if (line == "OFF") {
    g_servoX.detach();
    g_servoY.detach();
    updateEyeTarget(90, 90);
    Serial.println("OK OFF");
    return;
  }

  const int commaIdx = line.indexOf(',');
  if (commaIdx < 0) {
    return;
  }

  const String xPart = line.substring(0, commaIdx);
  const String yPart = line.substring(commaIdx + 1);

  const int xColon = xPart.indexOf(':');
  const int yColon = yPart.indexOf(':');
  if (xColon < 0 || yColon < 0) {
    return;
  }

  int xAngle = xPart.substring(xColon + 1).toInt();
  int yAngle = yPart.substring(yColon + 1).toInt();

  xAngle = constrain(xAngle, 0, 180);
  yAngle = constrain(yAngle, 0, 180);

  attachIfNeeded();
  g_servoX.write(xAngle);
  g_servoY.write(yAngle);
  updateEyeTarget(xAngle, yAngle);

  Serial.print("OK X:");
  Serial.print(xAngle);
  Serial.print(" Y:");
  Serial.println(yAngle);
}

}  // namespace

void setup() {
  Serial.begin(115200);

  g_tft.init();
  g_tft.setRotation(kScreenRotation);
  drawEyeBase();
  updateEyeTarget(90, 90);

  // Timers dedicados do LEDC para os servos (PWM 100% em hardware)
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);

  g_servoX.setPeriodHertz(50);
  g_servoY.setPeriodHertz(50);

  g_servoX.attach(kPinServoX, kPulseMinUs, kPulseMaxUs);
  g_servoY.attach(kPinServoY, kPulseMinUs, kPulseMaxUs);

  g_servoX.write(90);
  g_servoY.write(90);

  Serial.println("ESP32 servo controller pronto.");
}

void loop() {
  animateFace();
  while (Serial.available()) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      processCommand(g_inputLine);
      g_inputLine = "";
    } else if (c != '\r') {
      g_inputLine += c;
    }
  }
}