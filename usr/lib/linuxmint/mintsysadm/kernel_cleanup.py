#!/usr/bin/python3

from common.kernel_cleanup import load_cleanup_settings, run_cleanup


def main():
    settings = load_cleanup_settings()
    if settings.enabled:
        run_cleanup()


if __name__ == "__main__":
    main()
