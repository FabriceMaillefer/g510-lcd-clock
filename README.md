# G510 LCD Clock

Affiche l'heure, l'utilisation du CPU et de la RAM sur l'écran LCD (160×43, monochrome) du clavier **Logitech G510** sous Linux.

## Écrans et touches

| Touche | Action |
|---|---|
| **MR** (ou **M1**) / **M2** / **M3** | écran horloge / CPU / RAM (la LED de la touche indique l'écran actif) |
| bouton rond à gauche de l'écran | écran suivant |
| bouton 1 sous l'écran | écran précédent |
| bouton 2 | écran suivant |
| bouton 3 | vue alternative (grande heure, barres par thread, détail RAM/cache/swap) |
| bouton 4 | inverser les couleurs |

- **Horloge** : heure, secondes et date (vue alternative : `HH:MM` en grand)
- **CPU** : utilisation, température, fréquence moyenne et historique sur 160 s (vue alternative : une barre par thread)
- **RAM** : utilisation et historique (vue alternative : barres utilisée / cache / swap)

Le script écrit directement l'image dans le `/dev/hidraw` de l'interface 1 du clavier (rapport de sortie n°3, 991 octets).
Pas besoin de libg15 ni de libusb : le pilote noyau `hid-lg-g15` reste attaché, et les touches G et le rétroéclairage continuent de fonctionner.

## Dépendances

- Python 3
- [Pillow](https://pypi.org/project/pillow/) (`pacman -S python-pillow`)
- Police DejaVu (`pacman -S ttf-dejavu`)

## Utilisation

```sh
./g510_clock.py            # lance l'affichage (Ctrl+C l'arrête et efface l'écran)
./g510_clock.py --test     # motif de test
./g510_clock.py --clear    # efface l'écran
./g510_clock.py --keys     # affiche les rapports de touches bruts (débogage)
./g510_clock.py -v         # affiche chaque appui pris en compte et l’écran choisi
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

Touches (rapport d'entrée n°3, 5 octets) : les touches G1-G18 occupent les octets 1 à 3. Dans l'octet 3, M1/M2/M3/MR valent `0x10`/`0x20`/`0x40`/`0x80`.
Dans l'octet 4, les bits 0 à 4 sont le bouton rond puis les 4 boutons sous l'écran, de gauche à droite.

LED des touches M : rapport « feature » n°4 (`ioctl HIDIOCSFEATURE`), masque M1=`0x80`, M2=`0x40`, M3=`0x20`, MR=`0x10`.
