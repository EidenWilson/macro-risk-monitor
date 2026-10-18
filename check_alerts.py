"""Compare a fresh data.json with the previous one and write alert.md when something worth telling clients changed.
Exit code 0 always; the workflow opens a GitHub issue when alert.md exists (GitHub emails watchers of the repo)."""
import json
import pathlib
import sys

new = json.loads(pathlib.Path(sys.argv[1]).read_text())
old_path = pathlib.Path(sys.argv[2])
if not old_path.exists():
    sys.exit(0)
old = json.loads(old_path.read_text())
n, o = new["current"], old["current"]
lines = []
if n["regime"] != o["regime"]:
    lines.append(f"- Compass regime changed: **{o['regime']} -> {n['regime']}** (data through {n['asOf']}).")
if n.get("hmmState") != o.get("hmmState"):
    lines.append(f"- Statistical model state changed: **{o.get('hmmState')} -> {n.get('hmmState')}**.")
for key, label, limit in [("euribor", "Euribor 3M", 0.25), ("energy", "Energy inflation", 5), ("food", "Food inflation", 3), ("hicp", "Inflation", 1)]:
    if n.get(key) is not None and o.get(key) is not None and abs(n[key] - o[key]) >= limit:
        lines.append(f"- {label} moved from {o[key]} to {n[key]} in one update.")
if lines:
    body = "\n".join([f"Macro Risk Monitor update for data through {n['asOf']}:", ""] + lines +
                     ["", "Open the monitor to see how this changes each client's bad-year loss."])
    pathlib.Path("alert.md").write_text(body)
    print(body)
else:
    print("no alert")
