#!/bin/sh
set -e

case "$1" in
  scheduler)
    shift
    exec python -m property_hunter scheduler "$@"
    ;;
  *)
    exec python -m property_hunter "$@"
    ;;
esac
