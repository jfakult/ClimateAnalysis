#!/bin/bash
# Downloads NOAA's public ISD (Integrated Surface Database) raw hourly data,
# 2010-2025, into isd-raw/<year>/ next to this script.
#
#   ./pull_isd_data.sh
#
# Requires the AWS CLI (`aws`) on PATH. The bucket is public, so no AWS account
# or credentials are needed -- --no-sign-request skips auth entirely.
#
# This is a LOT of data -- roughly 10 GB/year, so ~160 GB for the full
# 2010-2025 range -- and will take a long time. It's safe to stop (Ctrl-C)
# and re-run: `aws s3 sync` only transfers files that are new or changed, so
# a re-run picks up wherever it left off, one year at a time.

set -euo pipefail
cd "$(dirname "$0")"

START_YEAR=2010
END_YEAR=2025
YEARS=$((END_YEAR - START_YEAR + 1))

echo "About to download NOAA ISD raw data for $START_YEAR-$END_YEAR into $(pwd)/isd-raw/"
echo "Rough size: ~10 GB/year x $YEARS years = ~$((YEARS * 10)) GB total. Make sure you have the disk space."
if [ -t 0 ]; then
  read -r -p "Press Enter to continue, or Ctrl-C to cancel... "
fi

for year in $(seq "$START_YEAR" "$END_YEAR"); do
  echo "== $year =="
  aws s3 sync --no-sign-request "s3://noaa-isd-pds/data/$year/" "isd-raw/$year/"
done
