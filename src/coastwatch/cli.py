"""Coastwatch command line.

  coastwatch eez               download the Bangladesh EEZ boundary
  coastwatch probe             confirm GFW API details (needs GFW_TOKEN)
  coastwatch fetch --year Y    pull a year of data into data/processed/Y/
  coastwatch summary --year Y  print the headline numbers
  coastwatch map --year Y      write maps/coastwatch-Y.html
"""

from __future__ import annotations

import argparse
from datetime import date


def main() -> None:
    parser = argparse.ArgumentParser(prog="coastwatch", description="Maritime watch for Bangladesh waters")
    sub = parser.add_subparsers(dest="command", required=True)

    eez = sub.add_parser("eez", help="download the Bangladesh EEZ boundary")
    eez.add_argument("--force", action="store_true", help="re-download even if cached")

    sub.add_parser("probe", help="confirm GFW API details (needs GFW_TOKEN)")

    default_year = date.today().year - 1
    for name, help_text in (("fetch", "pull a year of GFW data"),
                            ("summary", "print headline numbers"),
                            ("map", "build the HTML map")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--year", type=int, default=default_year)

    args = parser.parse_args()

    if args.command == "eez":
        from .regions import download_eez, eez_summary
        print(eez_summary(download_eez(force=args.force)))
    elif args.command == "probe":
        from .probe import run_probe
        run_probe()
    elif args.command == "fetch":
        from .fetch import run_fetch
        run_fetch(args.year)
    elif args.command == "summary":
        from .summary import print_summary
        print_summary(args.year)
    elif args.command == "map":
        from .mapping import build_map
        print(f"Wrote {build_map(args.year)}")
