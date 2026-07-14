"""Entry point: `python -m funnel`."""

from funnel.ui.app import FunnelApp


def main() -> None:
    FunnelApp().run()


if __name__ == "__main__":
    main()
