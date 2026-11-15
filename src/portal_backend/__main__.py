"""Run the development server with ``python -m portal_backend``."""

import os

from portal_backend.app import create_app

dev_mode = os.environ.get("PORTAL_DEV_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
app = create_app({"DEBUG": dev_mode})

if __name__ == "__main__":
    app.run(
        host=app.config["PORTAL_BIND_HOST"],
        port=app.config["PORTAL_BIND_PORT"],
        debug=app.config["DEBUG"],
    )
