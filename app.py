"""Vstopna tocka za Streamlit Cloud.

    streamlit run app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui.dashboard import main

main()
