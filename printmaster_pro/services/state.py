from contextlib import closing
from pathlib import Path
import json
import sqlite3
import logging
from PySide6.QtCore import QStandardPaths


class State:
    def __init__(self) -> None:
        self.root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.root.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=self.root/'printmaster.log', level=logging.INFO, encoding='utf-8')
        self.path = self.root/'settings.json'
        try:
            self.settings = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.settings,dict):
                raise ValueError('Settings must be an object.')
        except (ValueError,OSError):
            self.settings = {}
        with closing(self.db()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, kind TEXT, path TEXT, stamp TEXT DEFAULT CURRENT_TIMESTAMP)')

    def db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.root/'history.sqlite')

    def save(self) -> None:
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.settings,ensure_ascii=False,indent=2),encoding='utf-8')
        tmp.replace(self.path)

    def record(self, kind: str, path: Path) -> None:
        with closing(self.db()) as db, db:
            db.execute('INSERT INTO history(kind,path) VALUES (?,?)',(kind,str(path)))

    def history(self, kind: str | None = None) -> list[tuple]:
        with closing(self.db()) as db:
            return db.execute('SELECT stamp, kind, path FROM history WHERE (? IS NULL OR kind=?) ORDER BY id DESC LIMIT 100',(kind,kind)).fetchall()
