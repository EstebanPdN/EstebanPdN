# Profile card maintenance

`update_profile.py` renders the existing ASCII portrait and current public
GitHub statistics into dark and light SVG cards. Personal fields and the
website are maintained in `profile.json`. Only owned public repositories
contribute to the statistics; private projects are not disclosed.

```sh
python -m pip install -r requirements.txt
python scripts/update_profile.py
```

Run the command after publishing repository work to refresh the card's public
information. The existing GitHub workflow also runs monthly on the 15th, after
the configured birth-date day begins in the owner's timezone, and supports
manual dispatch. Uptime displays completed years and months. The default GitHub
background colors are `#0d1117` for dark mode and `#ffffff` for light mode.

For deterministic previews, use `--offline --date YYYY-MM-DD`; offline mode
reuses the last fetched public statistics and saved ASCII portrait. Generated
SVGs, the ASCII avatar and the statistics receipt remain tracked.
