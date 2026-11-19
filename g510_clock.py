#!/usr/bin/env python3
"""Affiche l'heure sur l'écran LCD (160x43, monochrome) d'un Logitech G510.

Écrit directement dans le /dev/hidraw de l'interface 1 du clavier (rapport de
sortie n°3, 991 octets). Pas besoin de libg15/libusb : le pilote noyau
hid-lg-g15 reste attaché, les touches G et le rétroéclairage continuent de
fonctionner.

Usage :
    ./g510_clock.py            # horloge, mise à jour chaque seconde
    ./g510_clock.py --test     # motif de test (cadre + damier)
    ./g510_clock.py --clear    # efface l'écran
"""
import argparse
import datetime
import glob
import os
import signal
import sys
import time

from PIL import Image, ImageDraw, ImageFont

W, H = 160, 43
REPORT_LEN = 992          # 1 octet d'ID de rapport (0x03) + 991 octets
LCD_OFFSET = 32           # les pixels commencent à l'octet 32
G510_IDS = ("0000046D:0000C22D", "0000046D:0000C22E")  # G510 / G510 (mode audio)

FONT_BIG = "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf"
FONT_SMALL = "/usr/share/fonts/TTF/DejaVuSans.ttf"

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


def find_lcd_hidraw():
    """Retourne le /dev/hidrawN de l'interface 1 (LCD) du G510."""
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        dev = os.path.realpath(os.path.join(node, "device"))
        try:
            with open(os.path.join(dev, "uevent")) as f:
                uevent = f.read()
        except OSError:
            continue
        if any(i in uevent for i in G510_IDS) and "input1" in uevent:
            return "/dev/" + os.path.basename(node)
    return None


def image_to_report(img):
    """Convertit une image 160x43 mode '1' au format du framebuffer G15/G510.

    Les pixels sont rangés par bandes de 8 lignes : l'octet
    LCD_OFFSET + (y // 8) * 160 + x contient le pixel (x, y) au bit y % 8.
    """
    buf = bytearray(REPORT_LEN)
    buf[0] = 0x03
    px = img.load()
    for y in range(H):
        base = LCD_OFFSET + (y >> 3) * W
        bit = 1 << (y & 7)
        for x in range(W):
            if px[x, y]:
                buf[base + x] |= bit
    return bytes(buf)


def render_clock(now, fonts):
    big, small = fonts
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)

    heure = now.strftime("%H:%M:%S")
    date = f"{JOURS[now.weekday()]} {now.day} {MOIS[now.month - 1]}"

    # Heure centrée en haut, date centrée en bas
    bb = d.textbbox((0, 0), heure, font=big)
    d.text(((W - (bb[2] - bb[0])) // 2 - bb[0], 1 - bb[1]), heure, font=big, fill=1)
    bb = d.textbbox((0, 0), date, font=small)
    d.text(((W - (bb[2] - bb[0])) // 2 - bb[0], H - 1 - bb[3]), date, font=small, fill=1)

    # Barre de progression des secondes sous l'heure
    d.line((0, 26, int((W - 1) * now.second / 59), 26), fill=1)
    return img


def render_test():
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W - 1, H - 1), outline=1)
    for y in range(4, H - 4, 4):
        for x in range(4 + (y // 4 % 2) * 4, W - 4, 8):
            d.rectangle((x, y, x + 3, y + 3), fill=1)
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="chemin hidraw (auto-détecté par défaut)")
    ap.add_argument("--test", action="store_true", help="affiche un motif de test")
    ap.add_argument("--clear", action="store_true", help="efface l'écran")
    args = ap.parse_args()

    dev = args.device or find_lcd_hidraw()
    if not dev:
        sys.exit("G510 introuvable (aucun hidraw 046d:c22d interface 1).")

    fd = os.open(dev, os.O_WRONLY)
    blank = image_to_report(Image.new("1", (W, H), 0))

    if args.clear or args.test:
        os.write(fd, image_to_report(render_test()) if args.test else blank)
        return

    def stop(*_):
        try:
            os.write(fd, blank)
        finally:
            sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    fonts = (ImageFont.truetype(FONT_BIG, 26), ImageFont.truetype(FONT_SMALL, 11))
    while True:
        now = datetime.datetime.now()
        try:
            os.write(fd, image_to_report(render_clock(now, fonts)))
        except OSError:
            # clavier débranché / réénuméré : on attend qu'il revienne
            os.close(fd)
            while not (dev := args.device or find_lcd_hidraw()):
                time.sleep(2)
            fd = os.open(dev, os.O_WRONLY)
            continue
        time.sleep(1 - datetime.datetime.now().microsecond / 1e6)


if __name__ == "__main__":
    main()
