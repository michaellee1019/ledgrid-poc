#!/usr/bin/env python3
"""Sample controller cadence and receiver display rates without changing playback."""
import argparse
import json
import math
import statistics
import time
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://ledgridwall.local:5000')
    parser.add_argument('--seconds', type=float, default=15)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 3600:
        parser.error('--seconds must be finite and between 0 and 3600')
    samples = []
    observations = []
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        with urlopen(args.base_url.rstrip('/') + '/api/v1/composer/operations/telemetry', timeout=5) as response:
            payload = json.load(response)
            observations.append(payload)
            status = payload["controller"]
        samples.append({'actual_fps': status.get('actual_fps'),
                        'target_fps': status.get('target_fps'),
                        'is_running': status.get('is_running')})
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    rates = [sample['actual_fps'] for sample in samples
             if isinstance(sample['actual_fps'], (int, float)) and math.isfinite(sample['actual_fps'])]
    first, last = observations[0], observations[-1]
    elapsed = (last['controller'].get('updated_at') or 0) - (first['controller'].get('updated_at') or 0)
    initial = {item['device_index']: item for item in first['diagnostics']['driver_stats'].get('devices', [])}
    receivers = []
    for item in last['diagnostics']['driver_stats'].get('devices', []):
        previous = initial.get(item['device_index'], {})
        before, after = previous.get('receiver_frames_displayed'), item.get('receiver_frames_displayed')
        rate = (after-before)/elapsed if elapsed > 0 and isinstance(before, int) and isinstance(after, int) and after >= before else None
        receivers.append({'receiver':item['device_index'], 'connected':item.get('receiver_connected'),
                          'display_counter_fps':rate, 'crc_errors':item.get('receiver_crc_errors'),
                          'fec_uncorrectable':item.get('receiver_fec_uncorrectable_packets')})
    print(json.dumps({'observation': 'controller cadence and receiver counters; visual smoothness requires observation',
                      'samples': samples, 'mean_controller_tick_fps': statistics.mean(rates) if rates else None,
                      'receiver_rates': receivers}))


if __name__ == '__main__':
    main()
