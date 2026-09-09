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

Se o arquivo continuar com `root root` e `0600`, ajuste o dispositivo atual
imediatamente e mantenha a regra para os próximos boots:

```bash
sudo chgrp input /dev/uinput
sudo chmod 0660 /dev/uinput
```

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