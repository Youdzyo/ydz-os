"""Couche « système » de YDZ OS.

Tout accès au monde réel (fichiers système, commandes, port série, réseau)
passe par un objet System. Trois implémentations :
  - System      : le vrai système (Raspberry Pi OS) ;
  - FakeSystem  : fichiers et commandes scriptés, pour les tests unitaires ;
  - SimSystem   : (voir sim.py) un nœud simulé vivant, pour `ydzd --sim`.
"""
import glob as _glob
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request


class Res:
    """Résultat d'une commande."""
    __slots__ = ("rc", "out", "err")

    def __init__(self, rc=0, out="", err=""):
        self.rc, self.out, self.err = rc, out, err

    @property
    def ok(self):
        return self.rc == 0

    def __repr__(self):
        return "Res(rc=%r, out=%r, err=%r)" % (self.rc, self.out[:80], self.err[:80])


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    """1.0.14 : les redirections ne sont jamais suivies ici ; l'appelant décide (liste d'hôtes autorisés)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# 1.0.18.1 : sur beaucoup de box (Livebox…), le Pi en Wi-Fi annonce une IPv6 qui ne fonctionne pas. urllib
# tente l'IPv6 d'abord, attend le délai complet, puis échoue au lieu de basculer sur l'IPv4 (« GitHub
# injoignable »). On force donc la résolution IPv4 pour nos téléchargements (même logique que ForceIPv4 d'apt).
def _connexion_ipv4(host, port, timeout, source):
    import http.client
    dernier = None
    for infos in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        try:
            return socket.create_connection(infos[4], timeout, source)
        except OSError as ex:
            dernier = ex
    raise dernier if dernier else OSError("aucune adresse IPv4 pour %s" % host)


def _ouvre_ipv4():
    import http.client
    import ssl

    class ConnexionHTTPS(http.client.HTTPSConnection):
        def connect(self):
            self.sock = _connexion_ipv4(self.host, self.port, self.timeout, self.source_address)
            if getattr(self, "_tunnel_host", None):
                self._tunnel()
            ctx = self._context or ssl.create_default_context()
            self.sock = ctx.wrap_socket(self.sock, server_hostname=self.host)

    class ConnexionHTTP(http.client.HTTPConnection):
        def connect(self):
            self.sock = _connexion_ipv4(self.host, self.port, self.timeout, self.source_address)

    class HandlerHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(ConnexionHTTPS, req)

    class HandlerHTTP(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(ConnexionHTTP, req)

    return HandlerHTTP(), HandlerHTTPS()


class System:
    sim = False

    # --- temps -----------------------------------------------------------
    def now(self):
        return time.time()

    def monotonic(self):
        return time.monotonic()

    def sleep(self, s):
        time.sleep(s)

    # --- fichiers --------------------------------------------------------
    def read(self, path, default=None):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return default

    def write(self, path, data, mode=None):
        """Écriture atomique (fichier temporaire + renommage).
        1.0.15 : le fichier temporaire a un nom aléatoire et naît avec O_EXCL (mkstemp) : un lien symbolique
        déposé à l'avance à sa place ne peut jamais être suivi ; le mode est posé sur le descripteur (fchmod)."""
        import tempfile
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".ydz-tmp", dir=d)
        try:
            # le fichier naît en 0600 : jamais lisible par tous, même une milliseconde (secrets)
            os.fchmod(fd, mode if mode is not None else 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def publier_secret(self, path, data, groupe):
        """1.0.15 : écrit un secret lisible par root et par le groupe `groupe` seulement (0440), dans un dossier
        de root (0750, groupe `groupe`). Refuse un dossier qui serait un lien symbolique ou appartiendrait à
        quelqu'un d'autre que root. Faux si le groupe n'existe pas (pas de kiosque sur ce Pi)."""
        import grp
        import stat
        import tempfile
        try:
            gid = grp.getgrnam(groupe).gr_gid
        except KeyError:
            return False
        d = os.path.dirname(path)
        try:
            os.mkdir(d, 0o750)
        except FileExistsError:
            pass
        st = os.lstat(d)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
            raise OSError("dossier %s refusé (lien symbolique ou mauvais propriétaire)" % d)
        os.chown(d, os.geteuid(), gid, follow_symlinks=False)
        os.chmod(d, 0o750)              # /run appartient à root : personne d'autre ne peut remplacer ce dossier
        fd, tmp = tempfile.mkstemp(prefix=".jeton.", suffix=".ydz-tmp", dir=d)
        try:
            os.fchmod(fd, 0o440)
            os.fchown(fd, os.geteuid(), gid)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True

    def append(self, path, data, mode=0o640):
        """Ajout en fin de fichier (journaux) : une seule écriture courte, pas de réécriture complète."""
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(data)

    def write_sys(self, path, data):
        """Écriture directe (sysfs : pas de renommage possible)."""
        try:
            with open(path, "w") as f:
                f.write(str(data))
            return True
        except OSError:
            return False

    def exists(self, path):
        return os.path.exists(path)

    def chown(self, path, user):
        """Donne le fichier à `user` (et à son groupe). Faux si l'utilisateur n'existe pas.
        1.0.15 : jamais à travers un lien symbolique (ouverture O_NOFOLLOW puis fchown)."""
        try:
            import pwd
            pw = pwd.getpwnam(user)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
            try:
                os.fchown(fd, pw.pw_uid, pw.pw_gid)
            finally:
                os.close(fd)
            return True
        except (KeyError, OSError):
            return False

    def listdir(self, path):
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def glob(self, pattern):
        return sorted(_glob.glob(pattern))

    def realpath(self, path):
        return os.path.realpath(path)

    def makedirs(self, path, mode=0o755):
        os.makedirs(path, mode=mode, exist_ok=True)

    def remove(self, path):
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return True
        except OSError:
            return False

    def statvfs(self, path):
        """(total, libre) en octets, ou None."""
        try:
            s = os.statvfs(path)
            return s.f_frsize * s.f_blocks, s.f_frsize * s.f_bavail
        except OSError:
            return None

    # --- commandes -------------------------------------------------------
    def which(self, name):
        return shutil.which(name, path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def run(self, argv, input=None, timeout=30, env=None):
        try:
            e = dict(os.environ, LC_ALL="C", LANG="C")
            if env:
                e.update(env)
            p = subprocess.run(argv, input=input, capture_output=True, text=True,
                               timeout=timeout, env=e, errors="replace")
            return Res(p.returncode, p.stdout, p.stderr)
        except FileNotFoundError:
            return Res(127, "", "commande introuvable : %s" % argv[0])
        except subprocess.TimeoutExpired:
            return Res(124, "", "délai dépassé : %s" % " ".join(argv))
        except OSError as ex:
            return Res(126, "", str(ex))

    def tcp_ouvert(self, ip, port, delai=0.5):
        import socket
        try:
            with socket.create_connection((ip, port), timeout=delai):
                return True
        except OSError:
            return False

    def spawn(self, argv):
        """Lance une commande détachée (extinction, redémarrage…)."""
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except OSError:
            return False

    # --- réseau ----------------------------------------------------------
    def hostname(self):
        return socket.gethostname().split(".")[0]

    def primary_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # aucun paquet n'est émis
            return s.getsockname()[0]
        except OSError:
            return ""
        finally:
            s.close()

    def resolve(self, host):
        try:
            return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
        except (OSError, IndexError):
            return None

    def udp_broadcast(self, addr, port, data):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(data, (addr, port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def http(self, method, url, body=None, headers=None, timeout=2.0, max_octets=1024 * 1024):
        """Requête HTTP simple. Retourne (code, dict|None). code 0 = injoignable.
        1.0.15 : la réponse est bornée (`max_octets`, 1 Mo par défaut) : un pair menteur ne remplit pas la mémoire."""
        data = None
        h = {"Accept": "application/json"}
        if headers:
            h.update(headers)
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            opener = urllib.request.build_opener(*_ouvre_ipv4())
            with opener.open(req, timeout=timeout) as r:
                raw = r.read(max_octets + 1)
                code = r.status
        except urllib.error.HTTPError as ex:
            raw, code = ex.read(max_octets + 1), ex.code
        except Exception:
            return 0, None
        if len(raw) > max_octets:
            return 0, None
        try:
            return code, json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return code, None

    def http_entetes(self, method, url, headers=None, timeout=8.0):
        """Requête dont on veut les en-têtes (registres Docker : Docker-Content-Digest, WWW-Authenticate).
        Retourne (code, {en-tête en minuscules: valeur}, corps JSON|None). code 0 = injoignable."""
        req = urllib.request.Request(url, method=method, headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code, hdr, raw = r.status, r.headers, r.read(65536) if method != "HEAD" else b""
        except urllib.error.HTTPError as ex:
            code, hdr, raw = ex.code, ex.headers, b""
        except Exception:
            return 0, {}, None
        entetes = {k.lower(): v for k, v in (hdr.items() if hdr else [])}
        try:
            corps = json.loads(raw.decode("utf-8", "replace")) if raw else None
        except ValueError:
            corps = None
        return code, entetes, corps

    def http_brut(self, method, url, headers=None, timeout=30.0, max_octets=None):
        """1.0.14 : requête « brute » SANS suivre les redirections. Retourne (code, {en-tête en minuscules: valeur},
        octets|None). code 0 = injoignable ; code -1 = réponse plus grosse que `max_octets` (rien n'est gardé)."""
        opener = urllib.request.build_opener(_SansRedirection, *_ouvre_ipv4())
        req = urllib.request.Request(url, method=method, headers=dict(headers or {}))
        try:
            r = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as ex:
            r = ex
        except Exception:
            return 0, {}, None
        try:
            code = getattr(r, "status", None) or r.code
            entetes = {k.lower(): v for k, v in (r.headers.items() if r.headers else [])}
            if max_octets is not None:
                try:
                    if int(entetes.get("content-length") or 0) > max_octets:
                        return -1, entetes, None
                except ValueError:
                    pass
            morceaux, n = [], 0
            while True:
                try:
                    b = r.read(262144)
                except Exception:
                    return 0, entetes, None
                if not b:
                    break
                n += len(b)
                if max_octets is not None and n > max_octets:
                    return -1, entetes, None
                morceaux.append(b)
            return code, entetes, b"".join(morceaux)
        finally:
            try:
                r.close()
            except Exception:
                pass

    def http_download_time(self, url, timeout=20, headers=None):
        """Télécharge et jette ; retourne (octets, secondes) ou None. Le chrono part à la réception
        des en-têtes : la poignée de main TLS ne fausse pas le débit."""
        n = 0
        try:
            req = urllib.request.Request(url, headers=dict(headers or {}))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                t0 = time.monotonic()
                while True:
                    b = r.read(262144)
                    if not b:
                        break
                    n += len(b)
        except Exception:
            return None
        return n, max(time.monotonic() - t0, 1e-6)

    def http_upload_time(self, url, octets, timeout=30, headers=None):
        """Envoie `octets` zéros en POST ; retourne (octets, secondes) ou None (connexion ouverte avant le chrono)."""
        import http.client
        from urllib.parse import urlsplit
        u = urlsplit(url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        c = cls(u.hostname, u.port, timeout=timeout)
        try:
            c.connect()
            corps = bytes(octets)
            h = {"Content-Type": "application/octet-stream", "Content-Length": str(octets)}
            h.update(headers or {})
            t0 = time.monotonic()
            c.request("POST", u.path + ("?" + u.query if u.query else ""), body=corps, headers=h)
            r = c.getresponse()
            r.read()
            dt = time.monotonic() - t0
            if r.status >= 400:
                return None
            return octets, max(dt, 1e-6)
        except Exception:
            return None
        finally:
            c.close()

    def http_latence(self, url, fois=5, timeout=5, headers=None):
        """Temps de réponse (ms) de `fois` petites requêtes sur une même connexion gardée ouverte
        (après une requête d'échauffement) : proche du ping, sans compter TCP ni TLS."""
        import http.client
        from urllib.parse import urlsplit
        u = urlsplit(url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        c = cls(u.hostname, u.port, timeout=timeout)
        chemin = u.path + ("?" + u.query if u.query else "")
        out = []
        try:
            for i in range(fois + 1):
                t0 = time.monotonic()
                c.request("GET", chemin, headers=dict(headers or {}))
                r = c.getresponse()
                dt = time.monotonic() - t0
                r.read()
                if r.status >= 400:
                    return []
                if i:
                    out.append(dt * 1000.0)
        except Exception:
            pass
        finally:
            c.close()
        return out

    def http_sondes(self, url, n=8, timeout=4, headers=None):
        """1.0.15 : `n` petites requêtes INDÉPENDANTES (une connexion chacune) ; retourne la liste des temps (ms)
        des réussites. La perte de paquets se déduit de n - len(retour). Pour le contrôle réseau horaire."""
        out = []
        for _ in range(n):
            t0 = time.monotonic()
            try:
                req = urllib.request.Request(url, headers=dict(headers or {}))
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    r.read(64)
                out.append((time.monotonic() - t0) * 1000.0)
            except Exception:
                pass
        return out

    def passerelle(self):
        """Adresse IP de la passerelle par défaut (routeur), ou None."""
        r = self.run(["ip", "route"], timeout=4)
        if r.ok:
            m = re.search(r"^default via (\S+)", r.out, re.M)
            if m:
                return m.group(1)
        return None

    # --- port série (badge) ----------------------------------------------
    def open_serial(self, path):
        return _Serial(path)

    def usb_ident(self, port):
        """« vid:pid:série » d'un /dev/ttyXXX (champs inconnus laissés vides), ou None."""
        try:
            dev = os.path.realpath("/sys/class/tty/%s/device" % os.path.basename(port))
            for up in (dev, os.path.dirname(dev), os.path.dirname(os.path.dirname(dev))):
                if os.path.exists(os.path.join(up, "idVendor")):
                    def lire(n):
                        try:
                            with open(os.path.join(up, n)) as f:
                                return f.read().strip().lower()
                        except OSError:
                            return ""
                    return "%s:%s:%s" % (lire("idVendor"), lire("idProduct"), lire("serial"))
        except OSError:
            pass
        return None

    def usb_vendor(self, port):
        """Identifiant fabricant USB (hex minuscule) d'un /dev/ttyXXX, ou None si inconnu."""
        try:
            dev = os.path.realpath("/sys/class/tty/%s/device" % os.path.basename(port))
            for up in (dev, os.path.dirname(dev), os.path.dirname(os.path.dirname(dev))):
                p = os.path.join(up, "idVendor")
                if os.path.exists(p):
                    with open(p) as f:
                        return f.read().strip().lower()
        except OSError:
            pass
        return None


class _Serial:
    """Port série USB CDC minimal (termios), sans pyserial."""

    def __init__(self, path):
        import fcntl
        import termios
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            a = termios.tcgetattr(self.fd)
            a[0] = 0                      # iflag : brut
            a[1] = 0                      # oflag
            a[2] = (termios.CS8 | termios.CREAD | termios.CLOCAL)  # sans HUPCL
            a[3] = 0                      # lflag
            a[4] = a[5] = termios.B115200
            a[6][termios.VMIN] = 0
            a[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, a)
            # DTR actif (le firmware n'émet que si un hôte écoute), RTS relâché
            # (évite de maintenir l'ESP32-S3 en reset).
            import struct
            fcntl.ioctl(self.fd, termios.TIOCMBIS, struct.pack("I", termios.TIOCM_DTR))
            fcntl.ioctl(self.fd, termios.TIOCMBIC, struct.pack("I", termios.TIOCM_RTS))
        except Exception:
            pass
        self._buf = b""

    def write(self, data):
        end = time.monotonic() + 1.0
        while data:
            try:
                n = os.write(self.fd, data)
                data = data[n:]
            except BlockingIOError:
                if time.monotonic() > end:
                    raise OSError("écriture série bloquée")
                time.sleep(0.02)

    def readline(self, timeout=1.0):
        import select
        end = time.monotonic() + timeout
        while b"\n" not in self._buf:
            left = end - time.monotonic()
            if left <= 0:
                return None
            r, _, _ = select.select([self.fd], [], [], left)
            if r:
                try:
                    chunk = os.read(self.fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise OSError("port série fermé")
                self._buf += chunk
                if len(self._buf) > 65536:
                    self._buf = self._buf[-4096:]
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8", "replace").strip()

    def flush_input(self):
        self._buf = b""
        try:
            while os.read(self.fd, 4096):
                pass
        except OSError:
            pass

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


class FakeSerial:
    def __init__(self, responder=None):
        self.sent = []
        self.responder = responder
        self._lines = []
        self.closed = False

    def write(self, data):
        if self.closed:
            raise OSError("fermé")
        self.sent.append(data)
        if self.responder:
            r = self.responder(data)
            if r:
                self._lines.append(r)

    def readline(self, timeout=1.0):
        return self._lines.pop(0) if self._lines else None

    def flush_input(self):
        self._lines = []

    def close(self):
        self.closed = True


class FakeSystem(System):
    """Système factice : fichiers en mémoire, commandes scriptées.

    `cmds` : liste de (préfixe argv, réponse). La réponse est un Res, une
    chaîne (stdout) ou une fonction(argv, input) -> Res|str. Le préfixe le
    plus long gagne. Toute commande exécutée est consignée dans `calls`.
    """
    sim = True

    def __init__(self, files=None, cmds=None, host="forge", ip="192.168.1.20"):
        self.files = dict(files or {})
        self.cmds = list(cmds or [])
        self.calls = []
        self.spawned = []
        self.t = 1_800_000_000.0
        self.mono = 1000.0
        self.host, self.ip = host, ip
        self.serials = {}
        self.udp = []
        self.http_routes = {}
        self.vfs = {"/": (32 * 10**9, 20 * 10**9)}
        self.bins = None  # None = tout existe

    def now(self):
        return self.t

    def monotonic(self):
        return self.mono

    def sleep(self, s):
        self.t += s
        self.mono += s

    def advance(self, s):
        self.sleep(s)

    def read(self, path, default=None):
        v = self.files.get(path, default)
        return v() if callable(v) else v

    def write(self, path, data, mode=None):
        self.files[path] = data

    def append(self, path, data, mode=None):
        v = self.files.get(path, "")
        self.files[path] = (v() if callable(v) else v) + data

    def write_sys(self, path, data):
        self.files[path] = str(data)
        return True

    def chown(self, path, user):
        self.__dict__.setdefault("chowned", []).append((path, user))
        return True

    def publier_secret(self, path, data, groupe):
        self.files[path] = data
        self.__dict__.setdefault("publies", []).append((path, groupe))
        return True

    def exists(self, path):
        if path in self.files:
            return True
        p = path.rstrip("/") + "/"
        return any(k.startswith(p) for k in self.files)

    def listdir(self, path):
        p = path.rstrip("/") + "/"
        return sorted({k[len(p):].split("/")[0] for k in self.files if k.startswith(p)})

    def glob(self, pattern):
        import fnmatch
        depth = pattern.count("/")
        names = set()
        for k in self.files:
            parts = k.split("/")
            for i in range(2, len(parts) + 1):
                names.add("/".join(parts[:i]))
        return sorted(n for n in names if n.count("/") == depth and fnmatch.fnmatch(n, pattern))

    def realpath(self, path):
        return self.files.get("@link:" + path, path)

    def makedirs(self, path, mode=0o755):
        self.files.setdefault(path.rstrip("/") + "/.dir", "")

    def remove(self, path):
        p = path.rstrip("/") + "/"
        ks = [k for k in self.files if k == path or k.startswith(p)]
        for k in ks:
            del self.files[k]
        return bool(ks)

    def statvfs(self, path):
        best = None
        for m in self.vfs:
            if path == m or path.startswith(m.rstrip("/") + "/") or m == "/":
                if best is None or len(m) > len(best):
                    best = m
        return self.vfs.get(best)

    def which(self, name):
        if self.bins is None or name in self.bins:
            return "/usr/bin/" + name
        return None

    def on(self, prefix, response):
        self.cmds.append((list(prefix), response))

    def run(self, argv, input=None, timeout=30, env=None):
        argv = list(argv)
        self.calls.append(argv)
        if self.bins is not None and argv and argv[0] not in self.bins:
            return Res(127, "", "commande introuvable : %s" % argv[0])
        best = None
        for prefix, resp in self.cmds:
            if argv[:len(prefix)] == prefix and (best is None or len(prefix) >= len(best[0])):
                best = (prefix, resp)
        if best is None:
            return Res(0, "", "")
        r = best[1]
        if callable(r):
            r = r(argv, input)
        if isinstance(r, Res):
            return r
        return Res(0, r or "", "")

    def ran(self, *prefix):
        """Vrai si une commande commençant par `prefix` a été exécutée."""
        p = list(prefix)
        return any(c[:len(p)] == p for c in self.calls)

    def spawn(self, argv):
        self.spawned.append(list(argv))
        return True

    def hostname(self):
        return self.host

    def primary_ip(self):
        return self.ip

    def resolve(self, host):
        return self.files.get("@dns:" + host)

    def udp_broadcast(self, addr, port, data):
        self.udp.append((addr, port, data))
        return True

    def http_entetes(self, method, url, headers=None, timeout=8.0):
        h = self.http_routes.get(("ENTETES", method, url))
        if h is None:
            return 0, {}, None
        return h(method, url, headers or {}) if callable(h) else h

    def http(self, method, url, body=None, headers=None, timeout=2.0, max_octets=None):
        h = self.http_routes.get(url) or self.http_routes.get((method, url))
        if h is None:
            return 0, None
        if callable(h):
            return h(method, url, body, headers or {})
        return 200, h

    def http_brut(self, method, url, headers=None, timeout=30.0, max_octets=None):
        h = self.http_routes.get(("BRUT", method, url))
        if h is None:
            return 0, {}, None
        code, ent, corps = h(method, url, headers or {}) if callable(h) else h
        if max_octets is not None and corps is not None and len(corps) > max_octets:
            return -1, ent, None
        return code, dict(ent or {}), corps

    def http_download_time(self, url, timeout=20, headers=None):
        return 16 * 1024 * 1024, 0.16

    def http_upload_time(self, url, octets, timeout=30, headers=None):
        return octets, 0.5

    def http_latence(self, url, fois=5, timeout=5, headers=None):
        return [12.0] * fois

    def http_sondes(self, url, n=8, timeout=4, headers=None):
        f = self.http_routes.get(("SONDES", url))
        return list(f) if f is not None else [11.0, 12.0, 13.0, 12.5, 11.5, 12.0, 13.5, 12.0][:n]

    def passerelle(self):
        return self.files.get("@passerelle", "192.168.1.1")

    def usb_vendor(self, port):
        return getattr(self, "vendors", {}).get(port)

    def usb_ident(self, port):
        return getattr(self, "idents", {}).get(port)

    def open_serial(self, path):
        s = self.serials.get(path)
        if s is None:
            raise OSError("pas de port %s" % path)
        s.closed = False
        return s
