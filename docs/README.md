# Zeta

Projeto de visão computacional e controle de servos e uma tela.

O sistema é dividido entre dois dispositivos:

- **Raspberry Pi**: captura a imagem da câmera, detecta rosto e mão, mostra o vídeo e calcula a posição desejada.
- **ESP32**: recebe os ângulos pela USB serial, controla dois servos com PWM por hardware e exibe os olhos em um display TFT ST7789V2.

Quando não há comandos de rastreamento, o ESP32 executa uma animação de espera com piscadas, bocejo, sono e sorriso.

## Estrutura

```text
zeta/
├── docs/README.md       # esta documentação
├── raspberry/main.py    # entrada compatível para iniciar o robô
├── raspberry/app/       # aplicação Python, servidor web e controle uinput
├── raspberry/models/    # modelos de visão e arquivos da câmera
├── raspberry/tools/     # scripts de teste e diagnóstico
├── raspberry/docs/      # instruções específicas do Raspberry
├── raspberry/requirements.txt # dependências instaladas pelo pip
└── esp32/
    ├── platformio.ini   # placa, bibliotecas e configuração do TFT
    └── src/main.cpp     # firmware do ESP32
```

## Componentes

- Raspberry Pi com câmera compatível com Picamera2
- ESP32 Doit DevKit V1
- Dois servos para os eixos X e Y
- Display TFT ST7789V2 de 1,69 polegada, resolução 240x280
- Fonte adequada para os servos
- Cabo USB entre o Raspberry Pi e o ESP32

> Use uma fonte externa para os servos quando necessário e una o GND da fonte ao GND do ESP32. Não alimente vários servos diretamente pelo pino de 3,3 V do ESP32.

## Ligações do ESP32

| Função | GPIO |
|---|---:|
| Servo X | 25 |
| Servo Y | 26 |
| TFT SCLK | 18 |
| TFT MOSI | 23 |
| TFT CS | 5 |
| TFT DC | 27 |
| TFT RST | 33 |
| TFT backlight | 32 |

A orientação do display é configurada como `3` no firmware. Se a imagem aparecer invertida ou com cores trocadas, confira a montagem e o valor `TFT_RGB_ORDER` em `esp32/platformio.ini`.

## Preparar e gravar o ESP32

Abra a pasta `esp32` no VS Code com a extensão PlatformIO instalada. As dependências são instaladas automaticamente a partir de `platformio.ini`:

- `ESP32Servo`
- `TFT_eSPI`

Compile e grave o firmware com:

```bash
cd esp32
pio run -t upload
```

Para acompanhar as mensagens do firmware:

```bash
pio device monitor
```

Ao iniciar, o ESP32 centraliza os servos em 90 graus e imprime uma mensagem de pronto.

## Preparar o Raspberry Pi

O programa usa Python 3, OpenCV, PySerial, Picamera2 e MediaPipe. O arquivo `raspberry/requirements.txt` contém as dependências instaladas pelo `pip`. O `Picamera2` e o OpenCV com suporte à janela gráfica devem ser instalados pelo sistema no Raspberry Pi:

```bash
sudo apt update
sudo apt install -y python3-venv python3-opencv python3-picamera2
cd raspberry
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

O parâmetro `--system-site-packages` permite que o ambiente virtual encontre o `picamera2` e o `opencv` instalados pelo `apt`, mantendo isoladas as dependências instaladas pelo `pip`. Para sair do ambiente virtual:

```bash
deactivate
```

Conecte o ESP32 por USB e confira o nome da porta:

```bash
ls /dev/ttyUSB* /dev/ttyACM*
```

A porta padrão no código é `/dev/ttyUSB0`. Se o dispositivo usar outro nome, altere `SERIAL_PORT` em `raspberry/main.py`.

O programa baixa automaticamente, na primeira execução, os arquivos necessários quando não os encontra na pasta `raspberry/models`:

- `models/haarcascade_frontalface_default.xml`, para detecção do rosto
- `models/hand_landmarker.task`, para detecção da mão

É necessário acesso à internet na primeira execução ou copiar esses arquivos para a pasta `raspberry`.

## Portal do usuário

Com o Zeta em execução, abra `http://IP_DO_RASPBERRY:8080` no telefone conectado à mesma rede. O portal é dividido em cinco áreas:

- **Início**: carinha do Zeta, resumo de CPU, memória, temperatura e chat.
- **Visão**: vídeo processado da câmera, com botão de tela cheia.
- **Computador**: captura do desktop Wayland em tela cheia, touchpad, cliques, arraste e teclado virtual.
- **Robô**: seleção dos modos e parada dos servos.
- **Sistema**: métricas contínuas do Raspberry e terminal de diagnóstico com comandos restritos.

As métricas são lidas diretamente do sistema Linux e mostram CPU, memória e temperatura enquanto a visão computacional ou o Ollama estão ativos.

Os modos **YouTube + mão**, **Spotify + mão** e **Navegador + mão** abrem o endereço correspondente no Chromium no Raspberry. Ao selecionar novamente o mesmo aplicativo, o Zeta apenas tenta trazer a janela existente para frente. Com um desses modos ativo, a ponta do dedo indicador move o ponteiro do desktop e a aproximação do polegar com o indicador (pinça) faz um clique esquerdo rapidamente; mantenha a pinça por um instante e abra a mão antes do próximo clique. Ao cancelar o menu, deixar o menu expirar ou pressionar `F`, o modo volta para **Seguir rosto** e o Chromium continua aberto. O Chromium precisa estar instalado e o usuário precisa ter acesso a `/dev/uinput`.

## Executar

```bash
cd raspberry
source .venv/bin/activate
python main.py
```

A janela mostra o vídeo da câmera, as caixas dos rostos detectados e os pontos/conexões da mão. Pressione `ESC` para sair.

A análise é feita em `640x480`. O quadro é ampliado em 2x com `INTER_NEAREST` e exibido na janela em `1280x960`.

### Opções

```bash
python3 main.py --no-detect
python3 main.py --no-track
python3 main.py --force-rgb-input
```

- `--no-detect`: desativa rosto e mão.
- `--no-track`: mantém a detecção visual, mas desativa o rastreamento automático do rosto.
- `--force-rgb-input`: trata a imagem capturada como RGB puro, útil para câmeras/configurações que não apresentam o comportamento padrão esperado.

## Controles manuais

Com a janela do vídeo em foco:

| Teclas | Movimento |
|---|---|
| `W` ou seta para cima | Y para cima |
| `S` ou seta para baixo | Y para baixo |
| `A` ou seta para esquerda | X para esquerda |
| `D` ou seta para direita | X para direita |
| Espaço | envia `OFF` e libera os servos |
| `ESC` | encerra o programa |

O rastreamento automático usa o maior rosto detectado. A posição é suavizada e convertida gradualmente para ângulos entre 0 e 180 graus, com limite operacional normalizado de 80% para evitar os extremos mecânicos.

O detector de mão usa o modo `VIDEO` do MediaPipe, mantendo o contexto entre quadros para reduzir o custo de processamento no Raspberry Pi e evitar que a captura fique acumulada.

## Protocolo serial

A comunicação usa **115200 baud**, uma linha por comando, terminada em `\\n`:

```text
X:<angulo>,Y:<angulo>
OFF
```

Exemplos:

```text
X:90,Y:90
X:30,Y:140
OFF
```

O ESP32 limita os ângulos ao intervalo `0..180` e responde:

```text
OK X:90 Y:90
OK OFF
```

O comando `OFF` desliga o sinal dos dois servos. O display continua funcionando e retorna à animação de espera depois do período de inatividade.

## Diagnóstico rápido

- **Porta serial não abre**: confirme `SERIAL_PORT`, cabo USB, permissões do usuário e se o monitor do PlatformIO não está usando a porta ao mesmo tempo.
- **Servos tremem ou reiniciam o ESP32**: use alimentação externa apropriada, mantenha o GND comum e evite alimentar os servos pela placa.
- **TFT sem imagem**: confira CS, DC, RST, backlight, alimentação e o driver ST7789 definido no `platformio.ini`.
- **Cores invertidas**: teste `TFT_RGB_ORDER=TFT_RGB` no lugar de `TFT_BGR`.
- **Câmera não inicia**: teste a câmera com as ferramentas do Picamera2 e confirme que `python3-picamera2` está instalado.
- **MediaPipe falha ao iniciar**: confirme a instalação e se `hand_landmarker.task` foi baixado corretamente.

## Fluxo de funcionamento

1. O Raspberry Pi captura um quadro da câmera.
2. O OpenCV localiza o maior rosto e o MediaPipe identifica a mão.
3. A posição do rosto é suavizada e transformada em estado X/Y.
4. O Raspberry Pi envia os ângulos ao ESP32 pela USB serial.
5. O ESP32 atualiza os servos e desloca os dois olhos no TFT.
6. Após um período sem rastreamento, o firmware inicia a animação de espera.
