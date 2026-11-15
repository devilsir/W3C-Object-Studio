from __future__ import annotations

import os

from wc3injector.debuglog import configure_logging, install_exception_hooks, install_faulthandler, log_exception, log_resource_snapshot
from wc3injector.ui.object_studio import ObjectStudio

LOGGER, SESSION_LOG = configure_logging()
install_exception_hooks(LOGGER)
install_faulthandler(LOGGER)
log_resource_snapshot(LOGGER, "startup", level=20)


def main() -> None:
    # Give Windows a stable app identity before Tk creates the top-level window.
    # This helps the custom WC3 icon replace the generic Python/Tk icon in the
    # title bar, Alt-Tab and taskbar grouping when launched via python app.py.
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DarkSir.WC3ObjectStudio")
        except Exception:
            LOGGER.debug("Could not set Windows AppUserModelID", exc_info=True)
    LOGGER.info("Entering Tk mainloop")
    try:
        ObjectStudio().mainloop()
        LOGGER.info("Tk mainloop exited normally")
    except Exception as exc:
        log_exception(LOGGER, "fatal app mainloop", exc)
        raise


if __name__ == "__main__":
    main()
