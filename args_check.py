"""Validators for command line argument values."""
import pathlib
import argparse


def valid_filepath(path):
    """Check if path is a valid file."""
    if not pathlib.Path(path).is_file():
        raise argparse.ArgumentTypeError(f"'{path}' is not a valid file.")
    return pathlib.Path(path)


def valid_dirpath(path):
    """Check if path is a valid directory."""
    if not pathlib.Path(path).is_dir():
        raise argparse.ArgumentTypeError(f"'{path}' is not a valid directory.")
    return pathlib.Path(path)
