import logging
import webbrowser
from typing import Optional

logging.basicConfig(level=logging.INFO)

def open_zoar_bathroom_website(url: Optional[str]):
    if url:
        try:
            webbrowser.open(url)
            logging.info(f"Opened {url} in the default browser.")
        except Exception as e:
            logging.error(f"Failed to open {url}: {e}")
    else:
        logging.warning("No URL provided.")

if __name__ == "__main__":
    url = "https://zoarbathroom.com"
    open_zoar_bathroom_website(url)
