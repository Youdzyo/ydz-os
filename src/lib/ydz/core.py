"""Briques communes : erreurs d'API, tâches longues, bus d'événements (SSE)."""
import queue
import secrets
import threading


class ApiError(Exception):
    """Erreur renvoyée telle quelle au client : {"erreur": message, "code": code}."""

    def __init__(self, status, message, code="erreur", **extra):
        Exception.__init__(self, message)
        self.status, self.message, self.code, self.extra = status, message, code, extra

    def body(self):
        d = {"erreur": self.message, "code": self.code}
        d.update(self.extra)
        return d


class Events:
    """Diffusion vers les clients SSE. Chaque abonné a une file bornée : un
    client lent perd les plus vieux événements, il ne bloque jamais le démon.
    1.0.15 : 8 flux au total, dont 2 au plus pour des visiteurs anonymes (un flux « écran local » ou « session »
    passe toujours ; un anonyme ne peut pas priver l'écran de la tour de son flux)."""
    MAX_CLIENTS = 8
    MAX_ANON = 2

    def __init__(self):
        self.subs = []
        self.lock = threading.Lock()

    def subscribe(self, prioritaire=True):
        with self.lock:
            if len(self.subs) >= self.MAX_CLIENTS:
                return None
            if not prioritaire and sum(1 for s in self.subs if not s[1]) >= self.MAX_ANON:
                return None
            q = queue.Queue(maxsize=40)
            self.subs.append((q, prioritaire))
            return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs = [s for s in self.subs if s[0] is not q]

    @property
    def listeners(self):
        return len(self.subs)

    def publish(self, kind, data):
        with self.lock:
            subs = [s[0] for s in self.subs]
        for q in subs:
            try:
                q.put_nowait((kind, data))
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait((kind, data))
                except (queue.Empty, queue.Full):
                    pass


class Task:
    def __init__(self, tasks, kind, title, target=None):
        self.tasks = tasks
        self.id = secrets.token_hex(4)
        self.kind, self.title, self.target = kind, title, target
        self.state = "en_cours"
        self.message = ""
        self.lines = []
        self.start = tasks.sys.now()
        self.end = None

    def log(self, text):
        for line in str(text or "").splitlines():
            line = line.rstrip()
            if line:
                self.lines.append(line[:300])
        del self.lines[:-60]
        self.tasks.changed(self)

    def as_dict(self, full=False):
        return {"id": self.id, "type": self.kind, "titre": self.title, "cible": self.target,
                "noeud": self.tasks.node_name, "etat": self.state, "message": self.message,
                "journal": self.lines if full else self.lines[-3:],
                "debut": int(self.start), "fin": int(self.end) if self.end else None}


class Tasks:
    """Tâches longues (installation d'app, formatage, mise à jour) dans un fil
    dédié. Une seule tâche par « verrou » à la fois (ex. « app:minecraft »)."""
    KEEP = 20

    def __init__(self, sys, node_name, on_change=None, sync=False):
        self.sys, self.node_name, self.on_change, self.sync = sys, node_name, on_change, sync
        self.items = []
        self.locks = set()
        self.lock = threading.Lock()

    def changed(self, task):
        if self.on_change:
            self.on_change(task)

    def start(self, kind, title, fn, target=None, lock=None):
        """fn(task) retourne un message de fin ; une exception = échec."""
        with self.lock:
            if lock and lock in self.locks:
                raise ApiError(409, "Une opération est déjà en cours ici. Attends qu'elle se termine.",
                               "occupe")
            if lock:
                self.locks.add(lock)
            t = Task(self, kind, title, target)
            self.items.append(t)
            done = [x for x in self.items if x.state != "en_cours"]
            for old in done[:max(0, len(self.items) - self.KEEP)]:
                self.items.remove(old)

        def run():
            try:
                t.message = fn(t) or "Terminé."
                t.state = "ok"
            except ApiError as ex:
                t.state, t.message = "erreur", ex.message
            except Exception as ex:  # une tâche ne doit jamais tuer le démon
                t.state, t.message = "erreur", "Erreur inattendue : %s" % ex
            finally:
                t.end = self.sys.now()
                with self.lock:
                    self.locks.discard(lock)
                self.changed(t)

        self.changed(t)
        if self.sync:
            run()
        else:
            threading.Thread(target=run, name="ydz-tache-" + t.id, daemon=True).start()
        return t

    def get(self, tid):
        with self.lock:
            return next((t for t in self.items if t.id == tid), None)

    def list(self):
        with self.lock:
            return [t.as_dict() for t in reversed(self.items)]

    def busy(self):
        with self.lock:
            return any(t.state == "en_cours" for t in self.items)
