# G510 LCD Clock

Affiche l'heure et la date sur l'écran LCD (160×43, monochrome) du clavier **Logitech G510** sous Linux.

Le script écrit directement l'image dans le `/dev/hidraw` de l'interface 1 du clavier (rapport de sortie n°3, 991 octets).
Pas besoin de libg15 ni de libusb : le pilote noyau `hid-lg-g15` reste attaché, et les touches G et le rétroéclairage continuent de fonctionner.

## Dépendances

- Python 3
- [Pillow](https://pypi.org/project/pillow/) (`pacman -S python-pillow`)
- Police DejaVu (`pacman -S ttf-dejavu`)

## Utilisation

```sh
./g510_clock.py            # horloge, mise à jour chaque seconde (Ctrl+C l'arrête et efface l'écran)
./g510_clock.py --test     # motif de test
./g510_clock.py --clear    # efface l'écran
```

## Permissions

L'utilisateur doit pouvoir écrire dans le `/dev/hidrawN` du clavier. Règle udev fournie :

```sh
sudo cp 70-g510.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

## Lancement automatique (service systemd utilisateur)

```sh
cp g510-clock.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now g510-clock.service
```

Le service suppose que le dépôt est dans `~/Documents/G510` ; sinon, adapte `ExecStart`.

## Format de l'écran

Le rapport fait 992 octets : `0x03`, puis 31 octets d'en-tête à zéro, puis 960 octets de pixels.
Les pixels sont rangés en 6 bandes de 8 lignes : le pixel (x, y) est le bit `y % 8` de l'octet `32 + (y / 8) * 160 + x`.
