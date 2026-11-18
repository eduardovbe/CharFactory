"""Session views and bounded navigation history, without storing unsaved content."""
from __future__ import annotations

from functools import wraps
from pathlib import Path

from PySide6.QtGui import QTextCursor

from ..project import ProjectError


def navigation_step(fn):
    """Group a compound jump into one history entry (including nested jumps)."""
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        history = getattr(self, "workspace", None)
        if history is None:
            return fn(self, *args, **kwargs)
        history.begin()
        try:
            return fn(self, *args, **kwargs)
        finally:
            history.end()
    return wrapped


class Workspace:
    LIMIT = 100

    def __init__(self, win):
        self.win = win
        self.views = {}
        self.entries = []
        self.index = -1
        self.depth = 0
        self.changing = False
        self.restoring = False

    def location(self):
        w = self.win
        c, page = w.ctx.char, w.current_page()
        if c is None or page is None:
            return None
        loc = {"project": str(c.def_path), "page": w.pages.index(page)}
        if page in w.code_pages:
            f = page.current()
            if f:
                ed = f["edit"]
                loc.update(file=str(f["path"]), position=ed.textCursor().position(),
                           scroll=ed.verticalScrollBar().value())
        elif page is w.anims:
            a = page.action()
            if a:
                loc.update(anim=a.number, frame=page.frame_nav.index())
        elif page is w.sprites:
            s = page.current()
            if s:
                loc["sprite"] = [s.group, s.item]
        elif page is w.sounds:
            s = page.sound()
            if s:
                loc["sound"] = [s.group, s.number]
        return loc

    def view(self):
        w = self.win
        loc = self.location()
        if loc is None:
            return None
        code = {}
        for page in w.code_pages:
            if page.files:
                code[str(w.pages.index(page))] = {
                    "file": str(page.current()["path"]),
                    "cursors": {str(f["path"]): [f["edit"].textCursor().position(),
                                                 f["edit"].verticalScrollBar().value()]
                                for f in page.files}}
        a, s, snd = w.anims.action(), w.sprites.current(), w.sounds.sound()
        return {"location": loc, "code": code,
                "anim": a.number if a else None, "frame": w.anims.frame_nav.index(),
                "sprite": [s.group, s.item] if s else None,
                "sound": [snd.group, snd.number] if snd else None}

    def remember(self):
        if self.win.ctx.char:
            view = self.view()
            if view:
                self.views[str(self.win.ctx.char.def_path)] = view

    def before_character(self):
        self.remember()
        if not self.depth and not self.changing:
            self.record()
        self.changing = True

    def after_character(self):
        c = self.win.ctx.char
        view = self.views.get(str(c.def_path)) if c else None
        try:
            if view:
                self.restore_view(view)
        finally:
            self.changing = False
        if not self.depth:
            self.record()

    def begin(self):
        if not self.depth and not self.changing and not self.restoring:
            self.record()
        self.depth += 1

    def end(self):
        self.depth -= 1
        if not self.depth:
            self.record()

    def record(self, *_):
        if self.depth or self.changing or self.restoring:
            return
        loc = self.location()
        if loc is None:
            return
        if self.index >= 0 and self.entries[self.index] == loc:
            return
        self.entries[self.index + 1:] = [loc]
        self.entries = self.entries[-self.LIMIT:]
        self.index = len(self.entries) - 1
        self.update_actions()

    def update_actions(self):
        w = self.win
        open_paths = {str(c.def_path) for c in w.ctx.projects}
        w.back_action.setEnabled(any(e["project"] in open_paths for e in self.entries[:self.index]))
        w.forward_action.setEnabled(any(e["project"] in open_paths for e in self.entries[self.index + 1:]))

    def travel(self, delta):
        # Keep the exact cursor/frame where the user left the current destination.
        loc = self.location()
        if loc and self.index >= 0:
            self.entries[self.index] = loc
        i = self.index + delta
        open_paths = {str(c.def_path) for c in self.win.ctx.projects}
        while 0 <= i < len(self.entries):
            if self.entries[i]["project"] in open_paths:
                self.restoring = True
                try:
                    self.restore_location(self.entries[i])
                    self.index = i
                finally:
                    self.restoring = False
                break
            i += delta
        self.update_actions()

    @staticmethod
    def restore_cursor(ed, position, scroll):
        position = position if isinstance(position, int) else 0
        scroll = scroll if isinstance(scroll, int) else 0
        position = max(0, min(position, ed.document().characterCount() - 1))
        block = ed.document().findBlock(position)
        ed.goto_line(block.blockNumber() + 1)  # unfolds the destination
        cur = QTextCursor(ed.document())
        cur.setPosition(position)
        ed.setTextCursor(cur)
        ed.verticalScrollBar().setValue(max(0, int(scroll)))

    def restore_location(self, loc):
        w = self.win
        if not isinstance(loc, dict):
            return
        c = next((c for c in w.ctx.projects if str(c.def_path) == loc.get("project")), None)
        if c is None:
            return
        w.commit_code()
        w.ctx.activate(c)
        pi = loc.get("page", 0)
        if not isinstance(pi, int) or not 0 <= pi < len(w.pages):
            pi = 0
        page = w.pages[pi]
        w.show_page(page)
        if page in w.code_pages:
            for i, f in enumerate(page.files):
                if str(f["path"]) == loc.get("file"):
                    page.tabs.setCurrentIndex(i)
                    self.restore_cursor(f["edit"], loc.get("position", 0), loc.get("scroll", 0))
                    break
        elif page is w.anims and loc.get("anim") is not None:
            page.select_action(loc["anim"])
            frame = loc.get("frame", 0)
            page.frame_nav.set_index(max(0, frame) if isinstance(frame, int) else 0)
        elif page is w.sprites and self.pair(loc.get("sprite")):
            page.select(*loc["sprite"])
        elif page is w.sounds and self.pair(loc.get("sound")):
            page.select(*loc["sound"])

    @staticmethod
    def pair(value):
        return isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(n, int) for n in value)

    def restore_view(self, view):
        w = self.win
        self.depth += 1
        try:
            code = view.get("code", {})
            if not isinstance(code, dict):
                code = {}
            for key, state in code.items():
                if not isinstance(key, str) or not key.isdigit() or int(key) >= len(w.pages) or not isinstance(state, dict):
                    continue
                page = w.pages[int(key)]
                if page not in w.code_pages:
                    continue
                if not page.files:
                    page.load()
                    page.set_issues(w.ctx.last_issues)
                for i, f in enumerate(page.files):
                    cursors = state.get("cursors", {})
                    pos = cursors.get(str(f["path"])) if isinstance(cursors, dict) else None
                    if self.pair(pos):
                        self.restore_cursor(f["edit"], *pos)
                    if str(f["path"]) == state.get("file"):
                        page.tabs.setCurrentIndex(i)
            if view.get("anim") is not None:
                w.anims.select_action(view["anim"])
                frame = view.get("frame", 0)
                w.anims.frame_nav.set_index(max(0, frame) if isinstance(frame, int) else 0)
            if self.pair(view.get("sprite")):
                w.sprites.select(*view["sprite"])
            if self.pair(view.get("sound")):
                w.sounds.select(*view["sound"])
            self.restore_location(view.get("location", {}))
        finally:
            self.depth -= 1

    def save_session(self):
        self.remember()
        paths = [str(c.def_path) for c in self.win.ctx.projects]
        self.win.ctx.settings["session"] = {
            "projects": [{"path": p, "view": self.views.get(p, {})} for p in paths],
            "active": str(self.win.ctx.char.def_path) if self.win.ctx.char else None}

    def restore_session(self):
        w = self.win
        session = w.ctx.settings.get("session") or {}
        if not w.ctx.settings.get("restore_session", True) or not isinstance(session, dict):
            return []
        failed = []
        self.restoring = True
        try:
            projects = session.get("projects", [])
            if not isinstance(projects, list):
                return failed
            for item in projects:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    continue
                path = item["path"]
                if not Path(path).is_file():
                    failed.append(path)
                    continue
                try:
                    if isinstance(item.get("view"), dict):
                        self.views[path] = item["view"]
                    w.ctx.open(path)
                except (OSError, ValueError, RuntimeError, ProjectError):
                    failed.append(path)
            active = next((c for c in w.ctx.projects if str(c.def_path) == session.get("active")), None)
            if active:
                w.ctx.activate(active)
        finally:
            self.restoring = False
        self.record()
        return failed
