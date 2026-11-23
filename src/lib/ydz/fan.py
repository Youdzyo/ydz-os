"""Ventilateur de la tour : courbe, modes, PWM matériel (contrat §3 et §5).

Câblage : GPIO18 -> 220 Ω -> grille d'un 2N7000 ; drain -> fil PWM du
ventilateur. Le transistor INVERSE le signal : GPIO haut = fil PWM à la masse
= ventilateur ralenti. Sans signal (GPIO bas, FORGE absent ou OS planté), le
fil PWM est tiré à l'état haut par le ventilateur : 100 %. C'est la sécurité.

Donc : rapport cyclique GPIO = 100 % - vitesse voulue.
"""

# mode -> (plancher %, T basse °C, T haute °C). « equilibre » = courbe du contrat.
MODES = {
    "silence": (20, 50.0, 75.0),
    "equilibre": (25, 45.0, 70.0),
    "performance": (40, 40.0, 60.0),
}
MODE_LABELS = {"silence": "Silence", "equilibre": "Équilibré", "performance": "Performance"}
HYSTERESIS = 2.0  # °C : on ne ralentit qu'après une vraie baisse


def duty_for(temp, mode="equilibre"):
    """Vitesse (%) pour une température. None (inconnue) -> 100 % par sécurité."""
    if temp is None:
        return 100
    floor, t0, t1 = MODES.get(mode, MODES["equilibre"])
    if temp <= t0:
        return floor
    if temp >= t1:
        return 100
    return int(round(floor + (100 - floor) * (temp - t0) / (t1 - t0)))


def curve_points(mode="equilibre"):
    floor, t0, t1 = MODES.get(mode, MODES["equilibre"])
    return [[30, floor], [t0, floor], [t1, 100], [85, 100]]


def gpio_duty_ns(speed_pct, period_ns, inverted=True):
    """Durée à l'état haut du GPIO pour obtenir `speed_pct` au ventilateur."""
    speed = max(0, min(100, int(speed_pct)))
    hi = (100 - speed) if inverted else speed
    return period_ns * hi // 100


class FanCurve:
    """Courbe avec hystérésis : monte tout de suite, descend avec retard."""

    def __init__(self, mode="equilibre"):
        self.mode = mode if mode in MODES else "equilibre"
        self._ref = None
        self.speed = 100

    def update(self, temp):
        if temp is None:
            self._ref = None
            self.speed = 100
            return self.speed
        if self._ref is None or temp > self._ref or temp < self._ref - HYSTERESIS:
            self._ref = temp
        self.speed = duty_for(self._ref, self.mode)
        return self.speed

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError("mode inconnu")
        self.mode = mode
        self._ref = None


class PwmFan:
    """PWM matériel via sysfs. Sur Pi 5, GPIO18 = PWM0 canal 2 du RP1
    (overlay `dtoverlay=pwm,pin=18,func=2`). Le numéro de pwmchip dépend du
    noyau : on le cherche à l'exécution (contrôleur à 4 canaux, adresse
    ...98000, différent de celui du ventilateur officiel en ...9c000)."""

    # canal du RP1 selon le GPIO
    CHANNELS = {12: 0, 13: 1, 18: 2, 19: 3, 14: 2, 15: 3}

    def __init__(self, sys, gpio=18, hz=25000, inverted=True):
        self.sys, self.gpio, self.inverted = sys, gpio, inverted
        self.period = int(1e9 // hz)
        self.chan = None
        self.error = None
        self.last = None

    def find_chip(self):
        cands = []
        for chip in self.sys.glob("/sys/class/pwm/pwmchip*"):
            real = self.sys.realpath(chip)
            try:
                npwm = int((self.sys.read(chip + "/npwm", "0") or "0").strip())
            except ValueError:
                npwm = 0
            score = 0
            if "98000.pwm" in real:
                score += 10          # RP1 PWM0 (Pi 5)
            if "9c000.pwm" in real or "cooling" in real:
                score -= 10          # RP1 PWM1 : ventilateur du Active Cooler
            if "7e20c000" in real or "fe20c000" in real:
                score += 5           # Pi 4 et antérieurs (2 canaux)
            if npwm == 4:
                score += 2
            cands.append((score, chip, npwm))
        cands.sort(reverse=True)
        if not cands or cands[0][0] < 0:
            return None, 0
        return cands[0][1], cands[0][2]

    def setup(self):
        chip, npwm = self.find_chip()
        if not chip:
            self.error = ("PWM matériel introuvable (/sys/class/pwm vide). Vérifie la ligne "
                          "« dtoverlay=pwm,pin=%d,func=2 » dans /boot/firmware/config.txt puis redémarre."
                          % self.gpio)
            return False
        ch = self.CHANNELS.get(self.gpio, 0) if npwm >= 4 else (0 if self.gpio in (12, 18) else 1)
        if ch >= max(npwm, 1):
            ch = 0
        base = "%s/pwm%d" % (chip, ch)
        if not self.sys.exists(base):
            self.sys.write_sys(chip + "/export", ch)
            for _ in range(20):          # udev met un instant à créer le dossier
                if self.sys.exists(base):
                    break
                self.sys.sleep(0.05)
        if not self.sys.exists(base):
            self.error = "Le canal PWM %d de %s ne s'exporte pas." % (ch, chip)
            return False
        self.chan = base
        # ordre imposé par le noyau : duty <= period à tout moment
        self.sys.write_sys(base + "/duty_cycle", 0)
        if not self.sys.write_sys(base + "/period", self.period):
            self.error = "Impossible de régler la période PWM sur %s." % base
            self.chan = None
            return False
        self.sys.write_sys(base + "/polarity", "normal")
        self.sys.write_sys(base + "/enable", 1)
        self.error = None
        self.last = None
        return True

    @property
    def available(self):
        return self.chan is not None

    def set(self, speed_pct):
        """Applique une vitesse. Sans PWM : rien à faire, le ventilateur est à 100 %."""
        if self.chan is None:
            return False
        speed = max(0, min(100, int(speed_pct)))
        if speed == self.last:
            return True
        ok = self.sys.write_sys(self.chan + "/duty_cycle", gpio_duty_ns(speed, self.period, self.inverted))
        if ok:
            self.last = speed
        else:
            self.error = "Écriture PWM refusée."
            self.chan = None
        return ok

    def release(self):
        """À l'arrêt du démon : GPIO bas = ventilateur à 100 % (sécurité)."""
        if self.chan:
            self.sys.write_sys(self.chan + "/duty_cycle", 0)
            self.last = None


class FanController:
    def __init__(self, sys, state, gpio=18, hz=25000, inverted=True):
        self.sys, self.state = sys, state
        self.pwm = PwmFan(sys, gpio, hz, inverted)
        self.curve = FanCurve(state.get("ventilo_mode", "equilibre"))
        self.hottest = None
        self.hottest_node = None
        self.test_until = 0
        self.pwm.setup()

    def tick(self, temps):
        """temps : {nom: température|None} des nœuds en ligne."""
        known = {k: v for k, v in temps.items() if v is not None}
        if known:
            self.hottest_node = max(known, key=known.get)
            self.hottest = known[self.hottest_node]
        else:
            self.hottest_node = self.hottest = None
        speed = self.curve.update(self.hottest)
        if self.sys.monotonic() < self.test_until:
            speed = 100
        if not self.pwm.available and self.sys.monotonic() > getattr(self, "_retry", 0):
            self._retry = self.sys.monotonic() + 60
            self.pwm.setup()
        self.pwm.set(speed)
        self.applied = speed
        return speed

    def set_mode(self, mode):
        self.curve.set_mode(mode)
        self.state.set("ventilo_mode", mode)

    def test(self, seconds=5):
        self.test_until = self.sys.monotonic() + seconds

    def status(self):
        speed = getattr(self, "applied", self.curve.speed)
        return {
            "mode": self.curve.mode,
            "mode_nom": MODE_LABELS[self.curve.mode],
            "vitesse": speed if self.pwm.available else 100,
            "consigne": speed,
            "pwm": self.pwm.available,
            "erreur": self.pwm.error,
            "temp": self.hottest,
            "noeud": self.hottest_node,
            "test": self.sys.monotonic() < self.test_until,
            "courbe": curve_points(self.curve.mode),
            "modes": [{"id": m, "nom": MODE_LABELS[m], "courbe": curve_points(m)} for m in MODES],
        }
