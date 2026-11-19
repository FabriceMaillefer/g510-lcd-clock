# G510 LCD Clock

Affiche l'heure et l'état du CPU, de la RAM et du GPU sur l'écran LCD (160×43, monochrome) du clavier **Logitech G510** sous Linux.

## Écrans

Quatre écrans, dans cet ordre, chacun avec une vue alternative :

- **Horloge** : heure, secondes et date (vue alternative : `HH:MM` en grand)
- **CPU** : utilisation, température, fréquence moyenne et historique sur 160 s (vue alternative : une barre par thread)
- **RAM** : utilisation et historique (vue alternative : barres utilisée / cache / swap)
- **GPU** (NVIDIA) : utilisation, température, consommation et historique (vue alternative : utilisation, VRAM, fréquence, état P, ventilateur si disponible)

## Touches

Seuls les boutons autour de l'écran sont utilisés :

| Bouton | Action |
|---|---|
| bouton rond à gauche de l'écran | écran suivant |
| bouton 1 sous l'écran | écran précédent |
| bouton 2 | écran suivant |
| bouton 3 | vue alternative |
| bouton 4 | inverser les couleurs |

Les touches M1, M2, M3 et MR ne sont pas utilisées, et leurs LED sont éteintes au démarrage.
Les contacts du G510 rebondissent : un appui n'est retenu que si le bouton est resté relâché au moins 100 ms juste avant (`DEBOUNCE` dans le script).

## GPU

Les mesures viennent de `nvidia-smi` (fourni par le pilote NVIDIA), sans dépendance Python supplémentaire.
`nvidia-smi` n'est appelé que lorsque l'écran GPU est affiché : l'interroger en permanence empêcherait un GPU de portable hybride de se mettre en veille.
L'historique ne couvre donc que le temps passé sur cet écran. Si le GPU est en veille, l'écran l'indique sans le réveiller.

Le script écrit directement l'image dans le `/dev/hidraw` de l'interface 1 du clavier (rapport de sortie n°3, 991 octets).
Pas besoin de libg15 ni de libusb : le pilote noyau `hid-lg-g15` reste attaché, et les touches G et le rétroéclairage continuent de fonctionner.

## Dépendances

- Python 3
- [Pillow](https://pypi.org/project/pillow/) (`pacman -S python-pillow`)
- Police DejaVu (`pacman -S ttf-dejavu`)
- Pour l'écran GPU : pilote NVIDIA avec `nvidia-smi` (`pacman -S nvidia-utils`)

## Utilisation

```sh
./g510_clock.py            # lance l'affichage (Ctrl+C l'arrête et efface l'écran)
./g510_clock.py --test     # motif de test
./g510_clock.py --clear    # efface l'écran
./g510_clock.py --keys     # affiche les rapports de touches bruts (débogage)
./g510_clock.py -v         # affiche chaque appui pris en compte et l'écran choisi
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

Touches (rapport d'entrée n°3, 5 octets) : les touches G1-G18 occupent les octets 1 à 3. Dans l'octet 3, M1/M2/M3/MR valent `0x10`/`0x20`/`0x40`/`0x80` (non utilisées ici).
Dans l'octet 4, les bits 0 à 4 sont le bouton rond puis les 4 boutons sous l'écran, de gauche à droite.

LED des touches M : rapport « feature » n°4 (`ioctl HIDIOCSFEATURE`), masque M1=`0x80`, M2=`0x40`, M3=`0x20`, MR=`0x10`. Le script les éteint.
