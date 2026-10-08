#!/bin/sh
# Build the echo-cancelling audio helper. Needs Xcode or the Command Line Tools.
set -e
cd "$(dirname "$0")"
swiftc -O -swift-version 5 -o claude-voice-audio AudioHelper.swift
echo "built $(pwd)/claude-voice-audio"
