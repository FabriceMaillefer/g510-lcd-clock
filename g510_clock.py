#!/usr/bin/env python3
"""Horloge, CPU et RAM sur l'écran LCD (160x43, monochrome) d'un Logitech G510.

Écrit directement dans le /dev/hidraw de l'interface 1 du clavier (rapport de
sortie n°3, 991 octets). Pas besoin de libg15/libusb : le pilote noyau
hid-lg-g15 reste attaché, les touches G et le rétroéclairage continuent de
fonctionner.

Touches :
    MR (ou M1) / M2 / M3  écran horloge / CPU / RAM (la LED indique l'écran actif)
    bouton rond         écran suivant
    boutons sous l'écran, de gauche à droite :
        1  écran précédent     2  écran suivant
        3  vue alternative     4  inverser les couleurs

Usage :
    ./g510_clock.py            # lance l'affichage
    ./g510_clock.py --test     # motif de test (cadre + damier)
    ./g510_clock.py --clear    # efface l'écran
    ./g510_clock.py --keys     # affiche les appuis bruts (débogage)
"""
import argparse
import collections
import datetime
import fcntl
import glob
import os
import select
import signal
import sys
import time

from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 160, 43
REPORT_LEN = 992          # 1 octet d'ID de rapport (0x03) + 991 octets
LCD_OFFSET = 32           # les pixels commencent à l'octet 32
G510_IDS = ("0000046D:0000C22D", "0000046D:0000C22E")  # G510 / G510 (mode audio)

# Rapport d'entrée n°3 (5 octets) : touches G, M, MR et boutons de l'écran
KEY_REPORT = 0x03
M_KEYS = {0x10: 0, 0x80: 0, 0x20: 1, 0x40: 2}  # octet 3 -> écran (M1 et MR : horloge)
LCD_ROUND, LCD_1, LCD_2, LCD_3, LCD_4 = 0x01, 0x02, 0x04, 0x08, 0x10  # octet 4

# Rapport « feature » n°4 : LED des touches M (M1=0x80, M2=0x40, M3=0x20, MR=0x10)
M_LEDS_REPORT = 0x04
M_LED_MASKS = (0x80 | 0x10, 0x40, 0x20)  # horloge : LED M1 + MR
DEBOUNCE = 0.10           # s de relâchement minimal entre deux appuis

FONT_DIR = "/usr/share/fonts/TTF/"
HISTORY = W               # un échantillon par seconde, un pixel par échantillon

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


def hidiocsfeature(length):
    """Numéro d'ioctl HIDIOCSFEATURE(len) (cf. linux/hidraw.h)."""
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x06


# --------------------------------------------------------------------------
# Matériel
# --------------------------------------------------------------------------

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


def set_m_leds(fd, mask):
    try:
        fcntl.ioctl(fd, hidiocsfeature(2), bytes([M_LEDS_REPORT, mask]))
    except OSError as e:
        print(f"LED M : {e}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Mesures système
# --------------------------------------------------------------------------

class CpuMonitor:
    def __init__(self):
        self.prev = self._read()
        self.total = 0.0
        self.per_cpu = [0.0] * (len(self.prev) - 1)
        self.history = collections.deque([0.0] * HISTORY, maxlen=HISTORY)

    @staticmethod
    def _read():
        stats = []
        with open("/proc/stat") as f:
            for line in f:
                if not line.startswith("cpu"):
                    break
                v = [int(x) for x in line.split()[1:]]
                idle = v[3] + v[4]                 # idle + iowait
                stats.append((sum(v[:8]), idle))   # sans guest (déjà dans user)
        return stats

    def sample(self):
        cur = self._read()
        usage = []
        for (t0, i0), (t1, i1) in zip(self.prev, cur):
            dt = t1 - t0
            usage.append(0.0 if dt <= 0 else 100.0 * (1 - (i1 - i0) / dt))
        self.prev = cur
        self.total, self.per_cpu = usage[0], usage[1:]
        self.history.append(self.total)

    @staticmethod
    def temperature():
        for hw in glob.glob("/sys/class/hwmon/hwmon*"):
            try:
                name = open(os.path.join(hw, "name")).read().strip()
                if name in ("coretemp", "k10temp", "zenpower"):
                    return int(open(os.path.join(hw, "temp1_input")).read()) / 1000
            except OSError:
                continue
        return None

    @staticmethod
    def frequency():
        mhz = []
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("cpu MHz"):
                    mhz.append(float(line.split(":")[1]))
        return sum(mhz) / len(mhz) / 1000 if mhz else None


class MemMonitor:
    def __init__(self):
        self.info = {}
        self.history = collections.deque([0.0] * HISTORY, maxlen=HISTORY)
        self.sample()

    def sample(self):
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                info[k] = int(v.split()[0]) * 1024
        self.info = info
        self.history.append(self.percent)

    @property
    def used(self):
        return self.info["MemTotal"] - self.info["MemAvailable"]

    @property
    def percent(self):
        return 100.0 * self.used / self.info["MemTotal"]


# --------------------------------------------------------------------------
# Dessin
# --------------------------------------------------------------------------

class Fonts:
    def __init__(self):
        self.clock = ImageFont.truetype(FONT_DIR + "DejaVuSansMono-Bold.ttf", 26)
        self.huge = ImageFont.truetype(FONT_DIR + "DejaVuSansMono-Bold.ttf", 40)
        self.text = ImageFont.truetype(FONT_DIR + "DejaVuSans.ttf", 11)
        self.small = ImageFont.truetype(FONT_DIR + "DejaVuSansMono.ttf", 10)


def go(n):
    return f"{n / 1024**3:.1f}"


def text_centered(d, y_top, text, font):
    bb = d.textbbox((0, 0), text, font=font)
    d.text(((W - (bb[2] - bb[0])) // 2 - bb[0], y_top - bb[1]), text, font=font, fill=1)


def text_at(d, x, y_top, text, font, right=False):
    bb = d.textbbox((0, 0), text, font=font)
    if right:
        x -= bb[2] - bb[0]
    d.text((x - bb[0], y_top - bb[1]), text, font=font, fill=1)


def graph(d, values, top, bottom):
    """Courbe pleine des 160 dernières valeurs (0-100 %), avec cadre."""
    h = bottom - top
    d.line((0, bottom, W - 1, bottom), fill=1)
    mid = top + h // 2                        # repère 50 % en pointillés
    for x in range(0, W, 4):
        d.point((x, mid), fill=1)
    for x, v in enumerate(values):
        y = bottom - round(h * min(max(v, 0), 100) / 100)
        if y < bottom:
            d.line((x, y, x, bottom), fill=1)


def bar(d, x0, y0, x1, y1, pct):
    d.rectangle((x0, y0, x1, y1), outline=1)
    fill = x0 + 1 + round((x1 - x0 - 2) * min(max(pct, 0), 100) / 100)
    if fill > x0 + 1:
        d.rectangle((x0 + 1, y0 + 1, fill - 1, y1 - 1), fill=1)


def render_clock(d, now, fonts, alt):
    if alt:
        text_centered(d, 1, now.strftime("%H:%M"), fonts.huge)
        return
    date = f"{JOURS[now.weekday()]} {now.day} {MOIS[now.month - 1]}"
    text_centered(d, 1, now.strftime("%H:%M:%S"), fonts.clock)
    d.line((0, 26, int((W - 1) * now.second / 59), 26), fill=1)
    bb = d.textbbox((0, 0), date, font=fonts.text)
    text_centered(d, H - 1 - (bb[3] - bb[1]), date, fonts.text)


def render_cpu(d, cpu, fonts, alt):
    temp, freq = cpu.temperature(), cpu.frequency()
    text_at(d, 0, 0, f"CPU {cpu.total:3.0f}%", fonts.small)
    info = []
    if temp is not None:
        info.append(f"{temp:.0f}°C")
    if freq is not None:
        info.append(f"{freq:.1f}GHz")
    text_at(d, W - 1, 0, " ".join(info), fonts.small, right=True)

    if not alt:
        graph(d, cpu.history, 11, H - 1)
        return
    # Une barre verticale par thread
    n = len(cpu.per_cpu)
    step = W / n
    top, bottom = 11, H - 1
    for i, v in enumerate(cpu.per_cpu):
        x0 = round(i * step)
        x1 = max(x0, round((i + 1) * step) - 2)
        y = bottom - round((bottom - top) * min(max(v, 0), 100) / 100)
        d.line((x0, bottom, x1, bottom), fill=1)
        if y < bottom:
            d.rectangle((x0, y, x1, bottom), fill=1)


def render_ram(d, mem, fonts, alt):
    i = mem.info
    text_at(d, 0, 0, f"RAM {mem.percent:3.0f}%", fonts.small)
    text_at(d, W - 1, 0, f"{go(mem.used)}/{go(i['MemTotal'])} Go", fonts.small, right=True)
    if not alt:
        graph(d, mem.history, 11, H - 1)
        return
    swap_used = i["SwapTotal"] - i["SwapFree"]
    cache = i.get("Cached", 0) + i.get("Buffers", 0)
    rows = [
        ("util", 100 * mem.used / i["MemTotal"], go(mem.used)),
        ("cache", 100 * cache / i["MemTotal"], go(cache)),
        ("swap", 100 * swap_used / i["SwapTotal"] if i["SwapTotal"] else 0, go(swap_used)),
    ]
    for n, (label, pct, val) in enumerate(rows):
        y = 12 + n * 10
        text_at(d, 0, y, label, fonts.small)
        bar(d, 34, y, 124, y + 7, pct)
        text_at(d, W - 1, y, val, fonts.small, right=True)


def render_test():
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W - 1, H - 1), outline=1)
    for y in range(4, H - 4, 4):
        for x in range(4 + (y // 4 % 2) * 4, W - 4, 8):
            d.rectangle((x, y, x + 3, y + 3), fill=1)
    return img


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------

class App:
    SCREENS = ("horloge", "cpu", "ram")

    def __init__(self, device, verbose=False):
        self.device = device
        self.verbose = verbose
        self.fd = None
        self.screen = 0
        self.alt = [False] * len(self.SCREENS)
        self.invert = False
        self.prev_keys = (0, 0)
        self.released_at = {}
        self.fonts = Fonts()
        self.cpu = CpuMonitor()
        self.mem = MemMonitor()

    def open(self):
        while True:
            dev = self.device or find_lcd_hidraw()
            if dev:
                try:
                    self.fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
                    self.update_leds()
                    return
                except OSError:
                    pass
            time.sleep(2)

    def update_leds(self):
        set_m_leds(self.fd, M_LED_MASKS[self.screen])

    def render(self):
        img = Image.new("1", (W, H), 0)
        d = ImageDraw.Draw(img)
        alt = self.alt[self.screen]
        if self.screen == 0:
            render_clock(d, datetime.datetime.now(), self.fonts, alt)
        elif self.screen == 1:
            render_cpu(d, self.cpu, self.fonts, alt)
        else:
            render_ram(d, self.mem, self.fonts, alt)
        if self.invert:
            img = ImageOps.invert(img.convert("L")).convert("1")
        os.write(self.fd, image_to_report(img))

    def debounce(self, *state):
        """Retourne, pour les octets 3 et 4, les bits nouvellement enfoncés.

        Les contacts du G510 rebondissent (rafales enfoncé/relâché espacées de
        20-40 ms, à l'appui comme au relâchement). Un appui ne compte que si la
        touche est restée relâchée au moins DEBOUNCE secondes juste avant.
        """
        now = time.monotonic()
        pressed = []
        for byte, (cur, prev) in enumerate(zip(state, self.prev_keys)):
            mask = 0
            for bit in range(8):
                m, key = 1 << bit, byte * 8 + bit
                if prev & m and not cur & m:
                    self.released_at[key] = now
                elif cur & m and not prev & m:
                    if now - self.released_at.get(key, float("-inf")) >= DEBOUNCE:
                        mask |= m
            pressed.append(mask)
        self.prev_keys = state
        return pressed

    def on_report(self, data):
        if len(data) != 5 or data[0] != KEY_REPORT:
            return
        down3, down4 = self.debounce(data[3], data[4])
        n = len(self.SCREENS)
        for mask, idx in M_KEYS.items():
            if down3 & mask:
                self.screen = idx
        if down4 & (LCD_ROUND | LCD_2):
            self.screen = (self.screen + 1) % n
        if down4 & LCD_1:
            self.screen = (self.screen - 1) % n
        if down4 & LCD_3:
            self.alt[self.screen] = not self.alt[self.screen]
        if down4 & LCD_4:
            self.invert = not self.invert
        if self.verbose and (down3 or down4):
            print(f"appui octet3={down3:02x} octet4={down4:02x} -> écran {self.SCREENS[self.screen]}"
                  f" alt={self.alt[self.screen]} invert={self.invert}", flush=True)
        if down3 or down4:
            self.update_leds()
            self.render()

    def shutdown(self, *_):
        if self.fd is not None:
            try:
                os.write(self.fd, image_to_report(Image.new("1", (W, H), 0)))
                set_m_leds(self.fd, 0)
            except OSError:
                pass
        sys.exit(0)

    def run(self):
        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)
        self.open()
        next_tick = time.monotonic()
        while True:
            try:
                now = time.monotonic()
                if now >= next_tick:
                    self.cpu.sample()
                    self.mem.sample()
                    self.render()
                    # se caler juste après le changement de seconde
                    next_tick = now + 1 - datetime.datetime.now().microsecond / 1e6 + 0.01
                r, _, _ = select.select([self.fd], [], [], max(0, next_tick - time.monotonic()))
                if r:
                    while True:
                        try:
                            data = os.read(self.fd, 64)
                        except BlockingIOError:
                            break
                        self.on_report(data)
            except OSError as e:
                # clavier débranché / réénuméré : on attend qu'il revienne
                print(f"erreur d'accès au clavier : {e}", file=sys.stderr, flush=True)
                try:
                    os.close(self.fd)
                except OSError:
                    pass
                self.open()


def dump_keys(dev):
    fd = os.open(dev, os.O_RDONLY)
    print(f"Lecture de {dev}, Ctrl+C pour quitter")
    prev = None
    try:
        while True:
            d = os.read(fd, 64)
            if d[0] == KEY_REPORT and len(d) == 5 and d != prev:
                print(f"{d.hex(' ')}   octet3={d[3]:08b} octet4={d[4]:08b}", flush=True)
                prev = d
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="chemin hidraw (auto-détecté par défaut)")
    ap.add_argument("--test", action="store_true", help="affiche un motif de test")
    ap.add_argument("--clear", action="store_true", help="efface l'écran")
    ap.add_argument("--keys", action="store_true", help="affiche les rapports de touches bruts")
    ap.add_argument("-v", "--verbose", action="store_true", help="affiche les appuis pris en compte")
    args = ap.parse_args()

    if args.test or args.clear or args.keys:
        dev = args.device or find_lcd_hidraw()
        if not dev:
            sys.exit("G510 introuvable (aucun hidraw 046d:c22d interface 1).")
        if args.keys:
            return dump_keys(dev)
        fd = os.open(dev, os.O_WRONLY)
        img = render_test() if args.test else Image.new("1", (W, H), 0)
        os.write(fd, image_to_report(img))
        return

    App(args.device, args.verbose).run()


if __name__ == "__main__":
    main()
