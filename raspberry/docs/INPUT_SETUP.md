# Controle global por uinput

O teclado e o mouse do telefone usam um dispositivo virtual Linux criado em
`/dev/uinput`. O processo do Zeta nao precisa rodar como root, mas o usuario
que inicia o programa precisa ter acesso de escrita ao dispositivo.

Instale a regra udev uma vez no Raspberry:

```bash
sudo cp docs/99-zeta-uinput.rules /etc/udev/rules.d/99-zeta-uinput.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add /sys/class/misc/uinput
```

Confirme que o pacote esta instalado no mesmo ambiente Python usado para iniciar
o Zeta:

```bash
source .venv/bin/activate
python -m pip install evdev
```

Se o arquivo continuar com `root root` e `0600`, ajuste o dispositivo atual
imediatamente e mantenha a regra para os próximos boots:

```bash
sudo chgrp input /dev/uinput
sudo chmod 0660 /dev/uinput
```

O usuario que inicia o servico tambem precisa estar no grupo `input`:

```bash
sudo usermod -aG input "$USER"
```

Depois de alterar grupos, encerre a sessao ou reinicie o Raspberry. Verifique
com `id` e confirme que `/dev/uinput` pertence a `root input` e tem modo `0660`.

Confira:

```bash
ls -l /dev/uinput
```

O esperado e algo como `root input` e permissao `crw-rw----`. Se ainda não
aparecer assim, reinicie o Raspberry para o udev reaplicar a regra:

```bash
sudo reboot
```

Depois disso, inicie o Zeta normalmente pelo ambiente virtual. O backend usa
uinput diretamente, portanto funciona em qualquer aplicacao do sistema e nao
depende de X11, Wayland ou `xdotool`.

## VNC por IP na sessao Wayland correta

O servico global `wayvnc.service` pode mostrar uma tela cinza nesta instalacao:
ele roda como usuario `vnc`, fora da sessao Wayland de `tiagodnzz`. Use um
servico do usuario, iniciado dentro da sessao grafica, para compartilhar a
mesma tela que o Zeta captura com `grim`.

Instale a unidade incluída no projeto e prepare uma cópia das chaves TLS que
seja legível somente pelo usuário da sessão:

```bash
mkdir -p ~/.config/zeta-wayvnc ~/.config/systemd/user
sudo cp /etc/wayvnc/config /etc/wayvnc/tls_key.pem /etc/wayvnc/tls_cert.pem /etc/wayvnc/rsa_key.pem ~/.config/zeta-wayvnc/
sudo chown -R "$USER:$USER" ~/.config/zeta-wayvnc
chmod 700 ~/.config/zeta-wayvnc
chmod 600 ~/.config/zeta-wayvnc/*.pem
cp docs/zeta-wayvnc.service ~/.config/systemd/user/zeta-wayvnc.service
```

Pare primeiro as instancias que ocupam a porta e o socket da sessao:

```bash
systemctl --user disable --now rpi-connect-wayvnc.service
sudo systemctl disable --now wayvnc-control.service wayvnc.service
```

Ative-o dentro da sessao do usuario:

```bash
systemctl --user daemon-reload
systemctl --user enable --now zeta-wayvnc.service
systemctl --user status zeta-wayvnc.service
```

No cliente VNC, use o IP do Raspberry e a porta padrao `5900`. O login/PAM e a
senha continuam sendo os definidos em `/etc/rpi-connect/wayvnc.config`.