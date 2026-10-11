#!/usr/bin/env python3
import argparse
import re
import sys
from collections import Counter


def count_words(text, ignore_case=False):
    if ignore_case:
        text = text.lower()
    return Counter(re.findall(r"[A-Za-z']+", text))


def main(argv=None):
    p = argparse.ArgumentParser(description="Print the most frequent words of a text file (or stdin).")
    p.add_argument("file", nargs="?", help="text file; reads stdin when omitted")
    p.add_argument("--top", type=int, default=10, help="how many words to print (default 10)")
    p.add_argument("-i", "--ignore-case", action="store_true", help="count 'The' and 'the' as one word")
    args = p.parse_args(argv)
    text = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
    for word, n in count_words(text, args.ignore_case).most_common(args.top):
        print(f"{n:5d} {word}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
