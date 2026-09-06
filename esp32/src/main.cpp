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
#include "icons.h"

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
constexpr uint16_t kMenuAccentColor = TFT_YELLOW;
constexpr uint8_t kMenuItemCount = 6;
const char* const kMenuLabels[kMenuItemCount] = {"SEGUIR MAO", "SEGUIR ROSTO", "CONTAR DEDOS", "OBJETOS", "DATA E HORA", "DESENHAR"};

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
bool g_menuVisible = false;
bool g_modeScreenVisible = false;
uint8_t g_menuIndex = 0;
String g_statusText = "Pronto";
String g_dialogVisibleText;
unsigned long g_dialogStartedAt = 0;
unsigned long g_lastDialogFaceFrame = 0;

constexpr int16_t kDialogBubbleX = 18;
constexpr int16_t kDialogBubbleY = 30;
constexpr int16_t kDialogBubbleWidth = 250;
constexpr int16_t kDialogBubbleHeight = 105;

void drawDialogBubbleFrame(bool clearArea) {
  const int16_t faceCenterX = 48;
  const int16_t bubbleBottom = kDialogBubbleY + kDialogBubbleHeight;
  if (clearArea) {
    g_tft.fillRect(8, 22, g_tft.width() - 16, 125, kBackgroundColor);
  }
  g_tft.drawRoundRect(kDialogBubbleX, kDialogBubbleY,
                      kDialogBubbleWidth, kDialogBubbleHeight, 10, kEyeColor);
  g_tft.fillTriangle(faceCenterX - 8, bubbleBottom - 1,
                     faceCenterX + 8, bubbleBottom - 1,
                     faceCenterX, bubbleBottom + 20, kBackgroundColor);
  g_tft.drawTriangle(faceCenterX - 8, bubbleBottom - 1,
                     faceCenterX + 8, bubbleBottom - 1,
                     faceCenterX, bubbleBottom + 20, kEyeColor);
}

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

// Rosto ampliado usado como avatar na tela de dialogo.
void drawMiniFace(int16_t centerX, int16_t centerY, bool blinking = false,
                  bool smiling = false) {
  const int16_t eyeWidth = 19;
  const int16_t eyeHeight = blinking ? 3 : (smiling ? 27 : 30);
  const int16_t cornerRadius = eyeWidth / 2;
  const int16_t eyeGap = 23;
  g_tft.fillRoundRect(centerX - eyeGap - eyeWidth / 2, centerY - eyeHeight / 2,
                       eyeWidth, eyeHeight, cornerRadius, kEyeColor);
  g_tft.fillRoundRect(centerX + eyeGap - eyeWidth / 2, centerY - eyeHeight / 2,
                       eyeWidth, eyeHeight, cornerRadius, kEyeColor);
  const int16_t mouthY = centerY + 25;
  for (int16_t x = -13; x <= 13; x += 2) {
    const int16_t y = mouthY - (x * x) / (smiling ? 42 : 62);
    g_tft.drawPixel(centerX + x, y, kMouthColor);
  }
}

void drawMenuIcon(uint8_t index, int16_t centerX, int16_t centerY) {
  if (index == 0) {
    g_tft.drawRoundRect(centerX - 16, centerY - 1, 32, 31, 10, kEyeColor);
    for (int8_t finger = -2; finger <= 2; finger++) {
      const int16_t height = finger == -2 ? 22 : 30;
      g_tft.drawRoundRect(centerX + finger * 11 - 4, centerY - height,
                          8, height + 18, 4, kEyeColor);
    }
    g_tft.drawLine(centerX - 27, centerY + 7, centerX - 14, centerY + 2, kEyeColor);
  } else if (index == 1) {
    g_tft.drawRoundRect(centerX - 35, centerY - 25, 70, 54, 22, kEyeColor);
    g_tft.fillCircle(centerX - 14, centerY - 2, 6, kEyeColor);
    g_tft.fillCircle(centerX + 14, centerY - 2, 6, kEyeColor);
    g_tft.drawLine(centerX - 15, centerY + 14, centerX + 15, centerY + 14, kEyeColor);
  } else if (index == 2) {
    g_tft.drawRoundRect(centerX - 27, centerY + 1, 54, 22, 9, kEyeColor);
    for (int8_t finger = -2; finger <= 2; finger++) {
      const int16_t height = 25 + (finger == 0 ? 9 : 0);
      g_tft.drawRoundRect(centerX + finger * 13 - 5, centerY - height / 2,
                          10, height, 5, kEyeColor);
    }
  } else if (index == 3) {
    g_tft.drawRect(centerX - 30, centerY - 22, 60, 44, kEyeColor);
    g_tft.drawLine(centerX - 22, centerY - 14, centerX - 10, centerY - 14, kEyeColor);
    g_tft.drawLine(centerX - 22, centerY - 14, centerX - 22, centerY - 5, kEyeColor);
    g_tft.drawCircle(centerX + 8, centerY + 3, 13, kEyeColor);
    g_tft.drawLine(centerX + 17, centerY + 12, centerX + 26, centerY + 20, kEyeColor);
  } else {
    g_tft.drawCircle(centerX, centerY, 29, kEyeColor);
    g_tft.fillCircle(centerX, centerY, 5, kEyeColor);
    g_tft.drawLine(centerX, centerY, centerX, centerY - 18, kEyeColor);
    g_tft.drawLine(centerX, centerY, centerX + 14, centerY + 10, kEyeColor);
    g_tft.fillCircle(centerX, centerY, 3, kBackgroundColor);
  }
}

void drawGestureIcon(int16_t centerX, int16_t centerY) {
  for (int16_t y = 0; y < kIconHeight; y++) {
    for (int16_t chunk = 0; chunk < 5; chunk++) {
      const uint16_t row = pgm_read_word(&iconGestureMaskChunks[y][chunk]);
      for (int16_t bit = 0; bit < 16; bit++) {
        if (!(row & (uint16_t(1) << bit))) continue;
        g_tft.drawPixel(centerX - kIconWidth / 2 + chunk * 16 + bit,
                        centerY - kIconHeight / 2 + y, kMenuAccentColor);
      }
    }
  }
}

const uint16_t* menuIconForIndex(uint8_t index) {
  switch (index) {
    case 0: return iconFrontHand;
    case 1: return iconFace;
    case 2: return iconPanToolAlt;
    case 3: return iconFitScreen;
    case 4: return iconSchedule;
    default: return nullptr;
  }
}

void drawFace(bool blinking) {
  g_tft.fillScreen(kBackgroundColor);
  drawEyeShape(g_leftEyeCenterX + g_currentOffsetX, g_eyeCenterY + g_currentOffsetY,
               kEyeColor, blinking);
  drawEyeShape(g_rightEyeCenterX + g_currentOffsetX, g_eyeCenterY + g_currentOffsetY,
               kEyeColor, blinking);
  drawMouth();
}

void drawModeScreen() {
  g_tft.fillScreen(kBackgroundColor);
  const int16_t screenWidth = g_tft.width();
  const int16_t screenHeight = g_tft.height();

  g_tft.setTextDatum(MC_DATUM);
  g_tft.setTextColor(kEyeColor, kBackgroundColor);
  g_tft.setTextSize(1);
  g_tft.drawString(kMenuLabels[g_menuIndex], screenWidth / 2, 14);

  // O rosto pequeno fica no canto fisico inferior esquerdo. No sistema de
  // coordenadas do painel (rotacao 3) esse canto corresponde a X alto.
  const int16_t faceCenterX = 48;
  const int16_t faceCenterY = screenHeight - 46;

  const int16_t bubbleX = kDialogBubbleX;
  const int16_t bubbleY = kDialogBubbleY;
  drawDialogBubbleFrame(false);

  g_tft.setTextDatum(TL_DATUM);
  g_tft.setTextSize(g_statusText.length() <= 5 ? 4 : 2);
  g_tft.setTextColor(kMenuAccentColor, kBackgroundColor);
  const int16_t textX = bubbleX + 10;
  const int16_t textY = bubbleY + 14;
  const uint8_t charsPerLine = g_statusText.length() <= 5 ? 5 : (kDialogBubbleWidth - 20) / 12;
  String text = g_dialogVisibleText;
  for (uint8_t line = 0; line * charsPerLine < text.length() && line < 4; line++) {
    const int16_t start = line * charsPerLine;
    g_tft.drawString(text.substring(start, min((int)(start + charsPerLine), (int)text.length())),
             textX, textY + line * (g_statusText.length() <= 5 ? 38 : 24));
  }

  drawMiniFace(faceCenterX, faceCenterY);
  g_tft.setTextDatum(TL_DATUM);
}

void animateDialog() {
  const unsigned long now = millis();
  if (now - g_lastDialogFaceFrame >= 80) {
    g_lastDialogFaceFrame = now;
    const int16_t faceCenterX = 48;
    const int16_t baseFaceY = g_tft.height() - 46;
    const int16_t bobPhase = (now / 160) % 20;
    const int16_t bobOffset = bobPhase <= 10 ? bobPhase - 5 : 15 - bobPhase;
    const bool blinking = (now % 4200) >= 3600 && (now % 4200) < 3740;
    const bool smiling = (now / 1800) % 3 != 0;
    g_tft.fillRect(10, baseFaceY - 26, 76, 70, kBackgroundColor);
    drawMiniFace(faceCenterX, baseFaceY + bobOffset, blinking, smiling);
  }

  if (g_dialogVisibleText.length() >= g_statusText.length()) return;
  if (now - g_dialogStartedAt < 55 * g_dialogVisibleText.length()) return;
  g_dialogVisibleText = g_statusText.substring(0, g_dialogVisibleText.length() + 1);
  const int16_t bubbleX = kDialogBubbleX;
  const int16_t bubbleY = kDialogBubbleY;
  const int16_t bubbleWidth = kDialogBubbleWidth;
  const int16_t textX = bubbleX + 10;
  const int16_t textY = bubbleY + 14;
  const uint8_t charsPerLine = g_statusText.length() <= 5 ? 5 : (bubbleWidth - 20) / 12;
  drawDialogBubbleFrame(true);
  g_tft.setTextDatum(TL_DATUM);
  g_tft.setTextColor(kMenuAccentColor, kBackgroundColor);
  g_tft.setTextSize(g_statusText.length() <= 5 ? 4 : 2);
  for (uint8_t line = 0; line * charsPerLine < g_dialogVisibleText.length() && line < 4; line++) {
    const int16_t start = line * charsPerLine;
    g_tft.drawString(
        g_dialogVisibleText.substring(start, min((int)(start + charsPerLine), (int)g_dialogVisibleText.length())),
        textX, textY + line * (g_statusText.length() <= 5 ? 38 : 24));
  }
}

void drawMenu() {
  g_tft.fillScreen(kBackgroundColor);
  g_tft.setTextDatum(MC_DATUM);
  const int16_t centerX = g_tft.width() / 2;
  const int16_t centerY = 116;
  g_tft.setTextColor(kEyeColor, kBackgroundColor);
  g_tft.setTextSize(2);
  g_tft.drawString("MENU", centerX, 18);
  g_tft.setTextSize(1);
  g_tft.drawString(String(g_menuIndex + 1) + "/" + String(kMenuItemCount), centerX, 39);

  g_tft.drawRoundRect(42, 62, g_tft.width() - 84, 112, 10, kMenuAccentColor);
  g_tft.setTextSize(4);
  g_tft.setTextColor(kMenuAccentColor, kBackgroundColor);
  g_tft.drawLine(25, centerY, 10, centerY, kMenuAccentColor);
  g_tft.drawLine(10, centerY, 18, centerY - 8, kMenuAccentColor);
  g_tft.drawLine(10, centerY, 18, centerY + 8, kMenuAccentColor);
  g_tft.drawLine(g_tft.width() - 25, centerY, g_tft.width() - 10, centerY, kMenuAccentColor);
  g_tft.drawLine(g_tft.width() - 10, centerY, g_tft.width() - 18, centerY - 8, kMenuAccentColor);
  g_tft.drawLine(g_tft.width() - 10, centerY, g_tft.width() - 18, centerY + 8, kMenuAccentColor);
  const int16_t iconX = centerX - kIconWidth / 2;
  const int16_t iconY = centerY - kIconHeight / 2;
  const uint16_t* menuIcon = menuIconForIndex(g_menuIndex);
  if (menuIcon != nullptr) {
    g_tft.pushImage(iconX, iconY, kIconWidth, kIconHeight, menuIcon);
  } else {
    drawGestureIcon(centerX, centerY);
  }
  g_tft.setTextSize(2);
  g_tft.setTextColor(kMenuAccentColor, kBackgroundColor);
  g_tft.drawString(kMenuLabels[g_menuIndex], centerX, 190);
  g_tft.setTextColor(kEyeColor, kBackgroundColor);
  g_tft.setTextSize(1);
  g_tft.drawString("JOIA CONFIRMA", centerX, 237);
  g_tft.setTextDatum(TL_DATUM);
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

  // O rastreamento move apenas os servos; os olhos possuem animacao propria.
  g_currentOffsetX = 0;
  g_currentOffsetY = 0;
}

void animateFace() {
  if (g_menuVisible || g_modeScreenVisible) {
    return;
  }
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

  if (line == "MODE:MENU") {
    g_menuVisible = true;
    g_modeScreenVisible = false;
    g_menuIndex = 1;
    g_idlePhase = -1;
    drawMenu();
    return;
  }
  if (line == "MODE:FACE") {
    g_menuVisible = false;
    g_modeScreenVisible = false;
    drawFace(g_lastBlinking);
    return;
  }
  if (line.startsWith("MODE:")) {
    g_menuVisible = false;
    g_modeScreenVisible = true;
    const String mode = line.substring(5);
    if (mode == "FOLLOW_HAND") g_menuIndex = 0;
    else if (mode == "FOLLOW_FACE") g_menuIndex = 1;
    else if (mode == "COUNT_FINGERS") g_menuIndex = 2;
    else if (mode == "OBJECT_DETECTION") g_menuIndex = 3;
    else if (mode == "DATE_TIME") g_menuIndex = 4;
    else if (mode == "DRAWING") g_menuIndex = 5;
    g_statusText = "Ativo";
    drawModeScreen();
    return;
  }
  if (line.startsWith("STATUS:TEXT:")) {
    const String newStatusText = line.substring(12);
    if (newStatusText == g_statusText) {
      return;
    }
    g_statusText = newStatusText;
    g_dialogVisibleText = "";
    g_dialogStartedAt = millis();
    if (g_modeScreenVisible && !g_menuVisible) {
      g_tft.fillRect(30, 44, g_tft.width() - 60, 86, kBackgroundColor);
    }
    return;
  }
  if (line.startsWith("STATUS:FINGERS:")) {
    g_statusText = "Dedos: " + line.substring(15);
    g_dialogVisibleText = "";
    g_dialogStartedAt = millis();
    if (g_modeScreenVisible) {
      g_tft.fillRect(30, 44, g_tft.width() - 60, 86, kBackgroundColor);
    }
    return;
  }
  if (line.startsWith("MENU:INDEX:")) {
    const int index = line.substring(11).toInt();
    if (index >= 0 && index < kMenuItemCount) {
      g_menuIndex = static_cast<uint8_t>(index);
      if (g_menuVisible) {
        drawMenu();
      }
    }
    return;
  }
  if (line == "MENU:CONFIRM") {
    Serial.print("OK MENU:");
    Serial.println(kMenuLabels[g_menuIndex]);
    return;
  }
  if (line == "MENU:CANCEL") {
    g_menuVisible = false;
    g_modeScreenVisible = false;
    drawFace(g_lastBlinking);
    return;
  }

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
  if (g_modeScreenVisible) {
    animateDialog();
  }
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