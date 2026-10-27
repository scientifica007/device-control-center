"""Extract the built installer and test its GTK integration without installing."""
from pathlib import Path
import subprocess
import sys
import tempfile


if __name__ == "__main__":
    package = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="dcc-deb-icon-") as directory:
        subprocess.run(["dpkg-deb", "-x", str(package), directory], check=True)
        subprocess.run([sys.executable, str(Path(__file__).with_name("gtk_icon.py")),
                        "--package-root", directory], check=True)
