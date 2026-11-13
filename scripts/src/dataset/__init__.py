"""
The synthetic eval dataset (see docs/adr/0009-measured-evals.md): fictional patients and
documents, rendered to the files that get uploaded, with an answer key and
an expected transcription for each.

- content.py: the patients and documents, written out by hand
- layout.py: blocks that become both the printed page and the transcription
- render.py: PDF, image-only PDF, PNG and JPEG output (PyMuPDF)
- keys.py: answer keys in the stored record format
- build.py: writes scripts/evals/dataset/
- load.py: uploads the dataset through the backend
"""
