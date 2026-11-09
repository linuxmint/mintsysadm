#!/usr/bin/python3

from aptkit.utils import use_default_solver

from common.kernel_cleanup import load_cleanup_settings, run_cleanup


def main():
    settings = load_cleanup_settings()
    if settings.enabled:
        use_default_solver()
        run_cleanup()


if __name__ == "__main__":
    main()
