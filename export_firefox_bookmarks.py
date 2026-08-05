#!/usr/bin/env python3
"""Export Firefox bookmarks from places.sqlite to Netscape-style HTML."""
import sqlite3
import html
import sys
from pathlib import Path

DB = "/tmp/places_export.sqlite"
OUT = Path.home() / "Desktop" / "firefox_bookmarks.html"

def export():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # type: 1=url, 2=folder, 3=separator
    cur.execute("""
        SELECT b.id, b.type, b.parent, b.position, b.title, b.fk, p.url
        FROM moz_bookmarks b
        LEFT JOIN moz_places p ON b.fk = p.id
        ORDER BY b.parent, b.position
    """)
    rows = cur.fetchall()
    conn.close()

    by_parent = {}
    for r in rows:
        pid = r["parent"]
        if pid not in by_parent:
            by_parent[pid] = []
        by_parent[pid].append(dict(r))

    def write_folder(parent_id, indent=0):
        lines = []
        children = by_parent.get(parent_id, [])
        for node in children:
            t = node["type"]
            title = node["title"] or "Untitled"
            title_esc = html.escape(title)
            if t == 1:  # URL
                url = node["url"] or ""
                lines.append(f'{"  " * indent}<DT><A HREF="{html.escape(url)}">{title_esc}</A>')
            elif t == 2:  # Folder
                lines.append(f'{"  " * indent}<DT><H3>{title_esc}</H3>')
                lines.append(f'{"  " * indent}<DL><p>')
                lines.append(write_folder(node["id"], indent + 1))
                lines.append(f'{"  " * indent}</DL><p>')
            elif t == 3:  # Separator
                lines.append(f'{"  " * indent}<HR>')
        return "\n".join(lines)

    # Firefox root: id=1. Its children are menu(2), toolbar(3), tags(4), unfiled(5), mobile(6)
    body = write_folder(1)

    html_out = f"""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<title>Firefox Bookmarks Export</title>
<h1>Bookmarks</h1>
<DL><p>
{body}
</DL><p>
"""
    OUT.write_text(html_out, encoding="utf-8")
    print(f"Exported to {OUT}")

if __name__ == "__main__":
    export()
