"""`python -m app` — start the server and open it in your browser."""
import os
import threading
import webbrowser

import uvicorn

port = int(os.getenv("PORT", "8000"))
if not os.getenv("NO_BROWSER"):
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
uvicorn.run("app.main:app", host=os.getenv("HOST", "127.0.0.1"), port=port)
