from __future__ import annotations

import json
import logging
import sys

from .settings import ConfigurationError, load_settings, parser


def main() -> None:
    p = parser()
    args = p.parse_args()
    try:
        settings = load_settings(args)
    except ConfigurationError as exc:
        p.error(str(exc))
    if args.print_config:
        print(json.dumps(settings.status(), ensure_ascii=True, indent=2))
        return
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    from .server import create_server

    create_server(settings).run(transport="stdio")


if __name__ == "__main__":
    main()
