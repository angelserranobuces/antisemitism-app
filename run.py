#!/usr/bin/env python3
"""
Launch the Antisemitism Risk Analyzer application.
Usage: python run.py
Then open http://localhost:8000 in your browser.
"""

import os
import sys
import subprocess

def main():
    # Ensure we're in the app directory
    app_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(app_dir)

    # Check data directory
    os.makedirs("data", exist_ok=True)

    print("=" * 60)
    print("  Newspaper Antisemitism Risk Analyzer")
    print("  IHRA-informed framing risk assessment")
    print("=" * 60)
    print()
    print("  Starting server at: http://localhost:8000")
    print("  Press Ctrl+C to stop")
    print()

    try:
        subprocess.run([
            sys.executable, "-m", "uvicorn",
            "app.main:app",
            "--host", "0.0.0.0",
            "--port", "8000",
            "--reload",
            "--timeout-keep-alive", "300",   # 5 min keep-alive for long analyses
        ], check=True)
    except KeyboardInterrupt:
        print("\nServer stopped.")

if __name__ == "__main__":
    main()
