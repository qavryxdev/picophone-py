from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from picophone import __version__
from picophone.call import CallController
from picophone.config import Config
from picophone.log import setup_logging
from picophone.ui.main_window import MainWindow


def main() -> int:
    QCoreApplication.setApplicationName("PicoPhone-Py")
    QCoreApplication.setApplicationVersion(__version__)
    QCoreApplication.setOrganizationName("PicoPhone-Py")

    config_dir = Path.home() / ".picophone"
    config_dir.mkdir(exist_ok=True)

    cfg = Config.load(config_dir / "picophone.toml", legacy_ini=Path("PicoPhone.ini"))
    setup_logging(config_dir / "picophone.log", enabled=cfg.ui.generate_log)

    if cfg.ui.generate_log:
        import logging
        logging.getLogger().info("=== PicoPhone-Py v%s starting ===", __version__)

    app = QApplication(sys.argv)
    for cand in ("picophone.ico", "picophone.png"):
        icon_path = Path(__file__).parent.parent / "assets" / "icons" / cand
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
            break

    controller = CallController(cfg)
    controller.start()

    win = MainWindow(cfg, controller)
    if "--tray" in sys.argv and cfg.ui.minimize_to_tray:
        win.start_in_tray()
    else:
        win.show()
    rc = app.exec()
    controller.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
