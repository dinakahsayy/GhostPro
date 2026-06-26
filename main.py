# Main entry point for the GhostPro LinkedIn AI application
import os

from app import create_app
from app.services.scheduler import start_scheduler

app = create_app()

if __name__ == "__main__":
    # Start the background scheduler (post generation + auto-publish ticks).
    # Set GHOSTPRO_SCHEDULER=0 to disable. The reloader is off so the scheduler
    # isn't started twice in debug mode.
    if os.getenv("GHOSTPRO_SCHEDULER", "1") == "1":
        start_scheduler(app)
    app.run(host="0.0.0.0", port=8080, debug=True, use_reloader=False)
