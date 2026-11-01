"""Compare a fresh data.json with the previous one and write alert.md when something worth telling clients changed.
Exit code 0 always; the workflow opens a GitHub issue when alert.md exists (GitHub emails watchers of the repo).

    python check_alerts.py docs/data.json previous.json

Improvements over the first version:
- A regime change in its first month is marked "provisional". The compass reads the sign of a 6-month change,
  which can flip back the next month; advisors should not re-plan on one month's reading.
- A flip straight back to the old regime is called out as noise instead of being reported as a fresh change.
- The thresholds are in one table and the logic is a function, so it can be unit-tested.
"""
import json
import pathlib
import sys

# key in data.json "current", label, smallest move worth an alert (same unit as the value)
THRESHOLDS = [
    ("euribor", "Euribor 3M", 0.25),
    ("energy", "Energy inflation", 5),
    ("food", "Food inflation", 3),
    ("hicp", "Inflation", 1),
]


def months_in_regime(new):
    """How many months in a row the latest regime has held, read from the regime history."""
    reg = [x for x in new.get("hist", {}).get("reg", []) if x]
    if not reg:
        return None
    n, last = 0, reg[-1]
    for x in reversed(reg):
        if x != last:
            break
        n += 1
    return n


def alert_lines(new, old):
    n, o = new["current"], old["current"]
    lines = []
    if n["regime"] != o["regime"]:
        held = months_in_regime(new)
        reg_hist = [x for x in new.get("hist", {}).get("reg", []) if x]
        # A -> B -> A: the old regime lasted one month and today's is the one before it
        flip_back = held == 1 and len(reg_hist) >= 3 and reg_hist[-2] == o["regime"] and reg_hist[-3] == n["regime"]
        tag = " (provisional: first month, confirm next update)" if held == 1 else ""
        lines.append(f"- Compass regime changed: **{o['regime']} -> {n['regime']}**{tag} (data through {n['asOf']}).")
        if flip_back:
            lines.append("  This reverses a change reported recently; treat both as noise until it holds two months.")
    if n.get("hmmState") != o.get("hmmState"):
        lines.append(f"- Statistical model state changed: **{o.get('hmmState')} -> {n.get('hmmState')}**.")
    for key, label, limit in THRESHOLDS:
        if n.get(key) is not None and o.get(key) is not None and abs(n[key] - o[key]) >= limit:
            lines.append(f"- {label} moved from {o[key]} to {n[key]} in one update.")
    return lines


def main(argv):
    new = json.loads(pathlib.Path(argv[1]).read_text())
    old_path = pathlib.Path(argv[2])
    if not old_path.exists():
        print("no previous data, no alert")
        return 0
    old = json.loads(old_path.read_text())
    lines = alert_lines(new, old)
    if lines:
        body = "\n".join([f"Macro Risk Monitor update for data through {new['current']['asOf']}:", ""] + lines +
                         ["", "Open the monitor to see how this changes each client's bad-year loss."])
        pathlib.Path("alert.md").write_text(body)
        print(body)
    else:
        print("no alert")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
