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
constexpr unsigned long kIdleCycleMs = 24000;
constexpr uint16_t kMenuAccentColor = TFT_YELLOW;
constexpr uint8_t kMenuItemCount = 6;
const char* const kMenuLabels[kMenuItemCount] = {"SEGUIR MAO", "SEGUIR ROSTO", "CONTAR DEDOS", "OBJETOS", "DATA E HORA", "DESENHAR"};
constexpr int16_t kMainEyeRadius = 8;
constexpr int16_t kMainEyeGap = 64;
constexpr int16_t kMainMouthWidth = 32;
constexpr int16_t kMainMouthYOffset = 36;
constexpr int16_t kMainMouthHeight = 4;

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
unsigned long g_lastDialogScrollAt = 0;
unsigned long g_dialogFinishedAt = 0;
uint16_t g_dialogScrollOffset = 0;
bool g_dialogSpeaking = false;
bool g_dialogThinking = false;

constexpr int16_t kDialogBubbleY = 170;
constexpr int16_t kDialogBubbleWidth = 250;
constexpr int16_t kDialogBubbleHeight = 58;
constexpr uint8_t kDialogTextSize = 2;
constexpr uint8_t kDialogVisibleChars = 21;
constexpr unsigned long kDialogTypeIntervalMs = 85;
constexpr unsigned long kDialogScrollIntervalMs = 180;
constexpr unsigned long kDialogHoldAfterSpeechMs = 3500;

int16_t dialogBubbleX() {
  return (g_tft.width() - kDialogBubbleWidth) / 2;
}

void drawDialogBubbleFrame(bool clearArea) {
  const int16_t faceCenterX = g_tft.width() / 2;
  const int16_t bubbleTop = kDialogBubbleY;
  const int16_t bubbleX = dialogBubbleX();
  if (clearArea) {
    g_tft.fillRect(bubbleX - 4, kDialogBubbleY - 22,
                   kDialogBubbleWidth + 8, kDialogBubbleHeight + 26,
                   kBackgroundColor);
  }
  g_tft.drawRoundRect(bubbleX, kDialogBubbleY,
                      kDialogBubbleWidth, kDialogBubbleHeight, 10, kEyeColor);
  g_tft.fillTriangle(faceCenterX - 8, bubbleTop + 1,
                     faceCenterX + 8, bubbleTop + 1,
                     faceCenterX, bubbleTop - 18, kBackgroundColor);
  g_tft.drawTriangle(faceCenterX - 8, bubbleTop + 1,
                     faceCenterX + 8, bubbleTop + 1,
                     faceCenterX, bubbleTop - 18, kEyeColor);
}

void drawEyeShape(int16_t centerX, int16_t centerY, uint16_t color, bool blinking) {
  if (blinking) {
    const int16_t radius = g_eyeWidth / 2;
    g_tft.fillCircle(centerX, centerY, radius, color);
    g_tft.fillRect(centerX - radius, centerY - radius, radius * 2 + 1, radius + 1,
                   kBackgroundColor);
    return;
  }
  g_tft.fillCircle(centerX, centerY, g_eyeWidth / 2, color);
}

void drawDialogFace(bool blinking, int16_t speakingMouthWidth = 0,
                    int16_t speakingMouthHeight = kMainMouthHeight) {
  const int16_t eyeCenterY = g_eyeCenterY - 8;
  drawEyeShape(g_leftEyeCenterX, eyeCenterY, kEyeColor, blinking);
  drawEyeShape(g_rightEyeCenterX, eyeCenterY, kEyeColor, blinking);
  const int16_t mouthY = eyeCenterY + kMainMouthYOffset;
  if (speakingMouthWidth > 0) {
    g_tft.drawRoundRect(
        g_tft.width() / 2 - speakingMouthWidth,
        mouthY - speakingMouthHeight / 2,
        speakingMouthWidth * 2, speakingMouthHeight,
        speakingMouthHeight / 2, kMouthColor);
  } else {
    g_tft.fillRoundRect(g_tft.width() / 2 - kMainMouthWidth, mouthY,
                        kMainMouthWidth * 2, kMainMouthHeight, 2, kMouthColor);
  }
}

          void drawDialogText() {
            g_tft.setTextDatum(TL_DATUM);
            g_tft.setTextColor(kMenuAccentColor, kBackgroundColor);
            g_tft.setTextSize(kDialogTextSize);
            const int end = min(
              static_cast<int>(g_dialogVisibleText.length()),
              static_cast<int>(g_dialogScrollOffset) + kDialogVisibleChars);
            g_tft.drawString(
              g_dialogVisibleText.substring(g_dialogScrollOffset, end),
                      dialogBubbleX() + 10, kDialogBubbleY + 20);
          }

void attachIfNeeded() {
  if (!g_servoX.attached()) {
    g_servoX.attach(kPinServoX, kPulseMinUs, kPulseMaxUs);
  }
  if (!g_servoY.attached()) {
    g_servoY.attach(kPinServoY, kPulseMinUs, kPulseMaxUs);
  }
}

void drawSleepingEye(int16_t centerX, int16_t centerY) {
  constexpr int16_t sleepingEyeWidth = 32;
  constexpr int16_t sleepingEyeHeight = 8;
  const int16_t left = centerX - sleepingEyeWidth / 2;
  const int16_t top = centerY - sleepingEyeHeight / 2;
  g_tft.fillRoundRect(left, top, sleepingEyeWidth, sleepingEyeHeight, 3,
                      kEyeColor);
}

void drawMouth() {
  const int16_t mouthCenterX = g_tft.width() / 2;
  const int16_t mouthY = g_eyeCenterY + kMainMouthYOffset;
  g_tft.fillRoundRect(mouthCenterX - kMainMouthWidth, mouthY,
                      kMainMouthWidth * 2, kMainMouthHeight, 2, kMouthColor);
}

// Rosto ampliado usado como avatar na tela de dialogo.
void drawMiniFace(int16_t centerX, int16_t centerY, bool blinking = false,
                  bool smiling = false, int16_t speakingMouthWidth = 0) {
  const int16_t eyeRadius = 15;
  const int16_t eyeGap = 23;
  if (blinking) {
    g_tft.fillCircle(centerX - eyeGap, centerY, eyeRadius, kEyeColor);
    g_tft.fillCircle(centerX + eyeGap, centerY, eyeRadius, kEyeColor);
    g_tft.fillRect(centerX - eyeGap - eyeRadius, centerY - eyeRadius,
                   eyeRadius * 2 + 1, eyeRadius + 1, kBackgroundColor);
    g_tft.fillRect(centerX + eyeGap - eyeRadius, centerY - eyeRadius,
                   eyeRadius * 2 + 1, eyeRadius + 1, kBackgroundColor);
  } else {
    g_tft.fillCircle(centerX - eyeGap, centerY, eyeRadius, kEyeColor);
    g_tft.fillCircle(centerX + eyeGap, centerY, eyeRadius, kEyeColor);
  }
  if (speakingMouthWidth > 0) {
    const int16_t mouthY = centerY + 8;
    g_tft.drawRoundRect(centerX - speakingMouthWidth, mouthY - 2,
                        speakingMouthWidth * 2, 5, 2, kMouthColor);
    return;
  }
  const int16_t mouthY = centerY + 8;
  for (int16_t x = -22; x <= 22; x += 2) {
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

  drawDialogBubbleFrame(false);

  drawDialogText();

  const bool animateSpeech = g_dialogSpeaking && !g_dialogThinking;
  drawDialogFace(false, animateSpeech ? 7 : 0,
                 animateSpeech ? 6 : kMainMouthHeight);
  g_tft.setTextDatum(TL_DATUM);
}

void animateDialog() {
  const unsigned long now = millis();
  if (now - g_lastDialogFaceFrame >= 80) {
    g_lastDialogFaceFrame = now;
    const bool blinking = (now % 4200) >= 3600 && (now % 4200) < 3740;
    const bool animateSpeech = g_dialogSpeaking && !g_dialogThinking;
    const int16_t speakingMouthWidth = animateSpeech
      ? 5 + ((now / 130) % 4) * 4
      : 0;
    const int16_t speakingMouthHeight = animateSpeech
      ? 4 + ((now / 170) % 4) * 2
      : kMainMouthHeight;
    g_tft.fillRect(4, 65, g_tft.width() - 8, 78, kBackgroundColor);
    drawDialogFace(blinking, speakingMouthWidth, speakingMouthHeight);
  }

  if (g_dialogVisibleText.length() < g_statusText.length()) {
    if (now - g_dialogStartedAt < kDialogTypeIntervalMs * g_dialogVisibleText.length()) {
      g_dialogSpeaking = true;
      return;
    }
    g_dialogVisibleText = g_statusText.substring(0, g_dialogVisibleText.length() + 1);
    g_dialogSpeaking = true;
    g_dialogFinishedAt = 0;
    drawDialogBubbleFrame(true);
    drawDialogText();
    return;
  }

  if (g_dialogThinking) {
    g_dialogSpeaking = false;
    g_dialogFinishedAt = 0;
    return;
  }

  const uint16_t lastScrollOffset = g_statusText.length() > kDialogVisibleChars
      ? g_statusText.length() - kDialogVisibleChars
      : 0;
  if (g_dialogScrollOffset < lastScrollOffset) {
    if (now - g_lastDialogScrollAt < kDialogScrollIntervalMs) {
      g_dialogSpeaking = true;
      return;
    }
    g_dialogSpeaking = true;
    g_lastDialogScrollAt = now;
    g_dialogScrollOffset++;
    g_dialogFinishedAt = 0;
    drawDialogBubbleFrame(true);
    drawDialogText();
    return;
  }

  g_dialogSpeaking = false;
  g_dialogThinking = false;
  if (g_dialogFinishedAt == 0) {
    g_dialogFinishedAt = now;
  } else if (now - g_dialogFinishedAt >= kDialogHoldAfterSpeechMs) {
    g_modeScreenVisible = false;
    g_dialogVisibleText = "";
    g_dialogScrollOffset = 0;
    drawFace(g_lastBlinking);
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
  const int16_t mouthY = g_eyeCenterY + kMainMouthYOffset;
  if (yawning) {
    g_tft.fillEllipse(mouthCenterX, mouthY + 3, 13, 16, kMouthColor);
  } else if (smiling) {
    for (int16_t x = -kMainMouthWidth; x <= kMainMouthWidth; x += 3) {
      const int16_t y = mouthY - (x * x) / 100;
      g_tft.fillCircle(mouthCenterX + x, y, 2, kMouthColor);
    }
  } else {
    g_tft.fillRoundRect(mouthCenterX - kMainMouthWidth, mouthY,
                        kMainMouthWidth * 2, kMainMouthHeight, 2, kMouthColor);
  }
}

void drawSleepZ(unsigned long now);

void drawSleepingFace(unsigned long now) {
  g_tft.fillScreen(kBackgroundColor);
  drawSleepingEye(g_leftEyeCenterX, g_eyeCenterY);
  drawSleepingEye(g_rightEyeCenterX, g_eyeCenterY);

  drawSleepZ(now);
}

void drawSleepZ(unsigned long now) {
  const int16_t zAreaLeft = g_leftEyeCenterX - 18;
  const int16_t zAreaTop = g_eyeCenterY - g_eyeHeight / 2 - 68;
  g_tft.fillRect(zAreaLeft - 6, zAreaTop - 4, 84, 56, kBackgroundColor);
  g_tft.setTextColor(kEyeColor, kBackgroundColor);
  g_tft.setTextSize(2);
  const int8_t zStep = (now / 180) % 18;
  for (int8_t index = 0; index < 3; index++) {
    const int16_t x = zAreaLeft + index * 22;
    const int16_t y = zAreaTop + 28 - ((zStep + index * 6) % 18) - index * 7;
    g_tft.setCursor(x, y < 2 ? 2 : y);
    g_tft.print("Z");
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
  g_leftEyeCenterX = halfWidth - kMainEyeGap;
  g_rightEyeCenterX = halfWidth + kMainEyeGap;
  g_eyeWidth = kMainEyeRadius * 2;
  g_eyeHeight = kMainEyeRadius * 2;
  g_eyeCornerRadius = g_eyeWidth / 2;
  g_maxOffset = 12;

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
  if (idlePosition >= 2200 && idlePosition < 4600) {
    phase = 1;
  } else if (idlePosition >= 4600 && idlePosition < 5200) {
    phase = 2;
  } else if (idlePosition >= 5200 && idlePosition < 5600) {
    phase = 3;
  } else if (idlePosition >= 5600 && idlePosition < 8000) {
    phase = 4;
  } else if (idlePosition >= 8000 && idlePosition < 8600) {
    phase = 5;
  } else if (idlePosition >= 8600 && idlePosition < 10100) {
    phase = 6;
  } else if (idlePosition >= 10100 && idlePosition < 11000) {
    phase = 7;
  } else if (idlePosition >= 11000 && idlePosition < 12000) {
    phase = 8;
  } else if (idlePosition >= 12000 && idlePosition < 21000) {
    phase = 9;
  } else {
    phase = 10;
  }

  if (phase == g_idlePhase) {
    if (phase == 9 && now - g_lastZFrame >= 180) {
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
    g_currentOffsetX = 0;
    drawIdleFace(0, false, false);
  } else if (phase == 3) {
    drawIdleFace(0, true, false);
  } else if (phase == 4) {
    g_currentOffsetX = -g_maxOffset * 0.7f;
    drawIdleFace(g_currentOffsetX, false, false);
  } else if (phase == 5) {
    g_currentOffsetX = 0;
    drawIdleFace(0, false, false);
  } else if (phase == 6) {
    drawIdleFace(0, false, true);
  } else if (phase == 7) {
    drawIdleFace(0, false, false, true);
  } else if (phase == 8) {
    drawSleepingFace(now);
  } else if (phase == 9) {
    drawSleepingFace(now);
  } else if (phase == 10) {
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
    g_dialogScrollOffset = 0;
    g_lastDialogScrollAt = 0;
    g_dialogFinishedAt = 0;
    g_dialogSpeaking = true;
    g_dialogThinking = newStatusText == "Pensando...";
    if (!g_menuVisible) {
      g_modeScreenVisible = true;
      drawModeScreen();
    }
    return;
  }
  if (line.startsWith("STATUS:FINGERS:")) {
    g_statusText = "Dedos: " + line.substring(15);
    g_dialogVisibleText = "";
    g_dialogStartedAt = millis();
    g_dialogScrollOffset = 0;
    g_lastDialogScrollAt = 0;
    g_dialogFinishedAt = 0;
    g_dialogSpeaking = true;
    g_dialogThinking = false;
    if (!g_menuVisible) {
      g_modeScreenVisible = true;
      drawModeScreen();
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